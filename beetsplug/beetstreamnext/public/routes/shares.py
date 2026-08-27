import os
import re
import secrets
import time
import zipfile
from pathlib import Path
from typing import Any, List

import flask
from flask import render_template

from .. import public_bp

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import ZIP_CACHE_DIR
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.utils.general import send_file
from beetsplug.beetstreamnext.api.serializers import IDMapper, map_song, map_album


def _safe_filename(name: Any) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', '_', str(name or '')).strip(' .')
    return cleaned or 'untitled'


def _resolve_song_path(item) -> Path:
    song_path = os.fsdecode(item.get('path', b''))
    path_obj = Path(song_path)
    if not path_obj.is_absolute():
        path_obj = app.config['root_directory'] / path_obj
    return path_obj


def _build_album_zip(items: List) -> Path:
    """Zip all songs of an album and returns the zip path."""
    zip_path = ZIP_CACHE_DIR / f'{secrets.token_hex(16)}.zip'

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
        for item in items:
            path_obj = _resolve_song_path(item)
            if not path_obj.is_file():
                bsn_logger.warning(f"Skipping missing file for album zip: '{path_obj}'")
                continue

            track = item.get('track') or 0
            title = _safe_filename(item.get('title') or path_obj.stem)
            arcname = f'{track:02d} - {title}{path_obj.suffix}' if track else f'{title}{path_obj.suffix}'
            zf.write(path_obj, arcname=arcname)

    return zip_path


@public_bp.route('/share/<share_id>')
def share_view(share_id: str) -> flask.Response:
    with database() as db:
        share = db.execute(
            """
            SELECT *
            FROM shares
            WHERE id = ?
            """, (share_id,)
        ).fetchone()

    if not share:
        flask.abort(404)

    if share['expires'] and share['expires'] < time.time():
        error = {
            'code': 410,
            'title': 'Share Expired',
            'message': 'This share has expired and is no longer accessible.',
        }
        return render_template('error.html', error=error), 410

    with database() as db:
        db.execute(
            """
            UPDATE shares
            SET visit_count = visit_count + 1
            WHERE id = ?
            """, (share_id,)
        )

    with database() as db:
        entry_rows = db.execute(
            """
            SELECT item_id
            FROM share_entries
            WHERE share_id = ?
            """, (share_id,)
        ).fetchall()

    entry_ids = [r['item_id'] for r in entry_rows]

    songs = []
    albums = []

    for entry_id in entry_ids:
        entry_type = IDMapper.get_type(entry_id)

        if entry_type == 'song':
            item = IDMapper.resolve_song(entry_id)
            if item:
                songs.append(map_song(item))

        elif entry_type == 'album':
            alb = IDMapper.resolve_album(entry_id)
            if alb:
                albums.append(map_album(alb, include_songs=True))

    return render_template('shares.html', share=share, songs=songs, albums=albums)


@public_bp.route('/share/<share_id>/download/<entry_id>')
def share_download(share_id: str, entry_id: str) -> flask.Response:

    with database() as db:
        share = db.execute(
            """
            SELECT *
            FROM shares
            WHERE id = ?
            """, (share_id,)
        ).fetchone()

    if not share or (share['expires'] and share['expires'] < time.time()):
        flask.abort(404)

    # Check if entry is shared (or if it belongs to an album that is shared)
    with database() as db:
        explicit_match = db.execute(
            """
            SELECT 1
            FROM share_entries
            WHERE share_id = ? AND item_id = ?
            """, (share_id, entry_id)
        ).fetchone()

    is_valid = bool(explicit_match)

    item = IDMapper.resolve_song(entry_id) if IDMapper.get_type(entry_id) == 'song' else None

    if not is_valid and item:
        album_id = item.get('album_id')

        if album_id:
            sub_album_id = IDMapper.mint_album(album_id)

            with database() as db:
                album_match = db.execute(
                    """
                    SELECT 1
                    FROM share_entries
                    WHERE share_id = ? AND item_id = ?
                    """, (share_id, sub_album_id)
                ).fetchone()

            is_valid = bool(album_match)

    if not is_valid:
        flask.abort(403)

    if not item:
        flask.abort(404)

    song_path = _resolve_song_path(item)

    return send_file(
        song_path,
        as_attachment=True,
        download_name=f"{item.title}.{item.format.lower()}"
    )


@public_bp.route('/share/<share_id>/download-album/<entry_id>')
def share_download_album(share_id: str, entry_id: str) -> flask.Response:

    with database() as db:
        share = db.execute("""SELECT * FROM shares WHERE id = ?""", (share_id,)).fetchone()

    if not share or (share['expires'] and share['expires'] < time.time()):
        flask.abort(404)

    if IDMapper.get_type(entry_id) != 'album':
        flask.abort(404)

    # Albums are only ever shared explicitly (unlike songs which can be valid via
    # their parent album)
    with database() as db:
        explicit_match = db.execute(
            """SELECT 1 FROM share_entries WHERE share_id = ? AND item_id = ?""",
            (share_id, entry_id)
        ).fetchone()

    if not explicit_match:
        flask.abort(403)

    album = IDMapper.resolve_album(entry_id)
    if not album:
        flask.abort(404)

    items = sorted(album.items(), key=lambda i: (i.get('disc', 1), i.get('track', 1)))
    if not items:
        flask.abort(404)

    zip_path = _build_album_zip(items)
    download_name = f"{_safe_filename(album.albumartist)} - {_safe_filename(album.album)}.zip"

    response = flask.send_file(zip_path, mimetype='application/zip', as_attachment=True, download_name=download_name)

    @response.call_on_close
    def _cleanup_zip() -> None:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError as e:
            bsn_logger.warning(f"Failed to clean up temporary zip '{zip_path}': {e}")

    return response


@public_bp.route('/share/<share_id>/cover/<entry_id>')
def share_cover(share_id: str, entry_id: str) -> flask.Response:

    with database() as db:
        share = db.execute("""SELECT * FROM shares WHERE id = ?""", (share_id,)).fetchone()

    if not share or (share['expires'] and share['expires'] < time.time()):
        flask.abort(404)

    # Validate that the requested entry belongs to this share
    with database() as db:
        explicit_match = db.execute(
            """SELECT 1 FROM share_entries WHERE share_id = ? AND item_id = ?""",
            (share_id, entry_id)
        ).fetchone()

    is_valid = bool(explicit_match)

    song_item = IDMapper.resolve_song(entry_id) if IDMapper.get_type(entry_id) == 'song' else None

    if not is_valid and song_item:
        album_id = song_item.get('album_id')
        if album_id:
            sub_album_id = IDMapper.mint_album(album_id)

            with database() as db:
                album_match = db.execute(
                    """SELECT 1 FROM share_entries WHERE share_id = ? AND item_id = ?""",
                    (share_id, sub_album_id)
                ).fetchone()

            is_valid = bool(album_match)

    if not is_valid:
        flask.abort(403)

    from beetsplug.beetstreamnext.core.images import send_album_art, round_image_size
    size = flask.request.args.get('size', default=0, type=int)
    rounded_size = round_image_size(size)

    if IDMapper.get_type(entry_id) == 'album':
        album = IDMapper.resolve_album(entry_id)
        response = send_album_art(album.id, rounded_size) if album else None
        if response:
            return response

    elif song_item and song_item.get('album_id'):
        response = send_album_art(song_item.get('album_id'), rounded_size)
        if response:
            return response

    flask.abort(404)
