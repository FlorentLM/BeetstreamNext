import re
import secrets
import time
import zipfile
from pathlib import Path
from typing import Any, List

import flask
from flask import render_template

from .. import public_bp

from beetsplug.beetstreamnext.constants import ZIP_CACHE_DIR
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.utils.general import send_file
from beetsplug.beetstreamnext.api.idmapper import IDMapper

from beetsplug.beetstreamnext.api.idmapper import beets_abspath


def _safe_filename(name: Any) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', '_', str(name or '')).strip(' .')
    return cleaned or 'untitled'


def _is_shared(share_id: str, entry_id: str) -> bool:
    """Whether entry_id is shared or is part of an album that is shared."""

    with database() as db:
        explicit_match = db.execute(
            """
            SELECT 1 
            FROM share_entries 
            WHERE share_id = ? AND item_id = ?
            """, (share_id, entry_id)
        ).fetchone()

    if explicit_match:
        return True

    entry_type, item = IDMapper.resolve(entry_id)
    if entry_type != 'song':
        return False

    album_id = item.get('album_id') if item else None
    if not album_id:
        return False

    sub_album_id = IDMapper.mint_album(album_id)
    with database() as db:
        album_match = db.execute(
            """
            SELECT 1 
            FROM share_entries 
            WHERE share_id = ? AND item_id = ?
            """, (share_id, sub_album_id)
        ).fetchone()

    return bool(album_match)


def _zip_album(items: List) -> Path:
    """Zip all songs of an album and returns the zip path."""
    zip_path = ZIP_CACHE_DIR / f'{secrets.token_hex(16)}.zip'

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
        for item in items:
            path_obj = beets_abspath(item)
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

    resolved_songs, resolved_albums = IDMapper.resolve_share(entry_ids)
    songs = [IDMapper.map_song(s) for s in resolved_songs]
    albums = [IDMapper.map_album(a, include_songs=True) for a in resolved_albums]

    return render_template('shares.html', share=share, songs=songs, albums=albums)


@public_bp.route('/share/<share_id>/download/<entry_id>')
def share_download(share_id: str, entry_id: str) -> flask.Response | None:

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

    if not _is_shared(share_id, entry_id):
        flask.abort(403)

    entry_type, item = IDMapper.resolve(entry_id)
    if entry_type != 'song' or not item:
        flask.abort(404)

    song_path = beets_abspath(item)

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

    entry_type, album = IDMapper.resolve(entry_id)
    if entry_type != 'album':
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

    if not album:
        flask.abort(404)

    items = sorted(album.items(), key=lambda i: (i.get('disc', 1), i.get('track', 1)))
    if not items:
        flask.abort(404)

    zip_path = _zip_album(items)
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

    if not _is_shared(share_id, entry_id):
        flask.abort(403)

    from beetsplug.beetstreamnext.core.images import send_album_art, round_image_size
    size = flask.request.args.get('size', default=0, type=int)
    rounded_size = round_image_size(size)

    entry_type, entry = IDMapper.resolve(entry_id)

    if entry_type == 'album':
        response = send_album_art(entry.id, rounded_size) if entry else None
        if response:
            return response

    elif entry_type == 'song':
        if entry and entry.get('album_id'):
            response = send_album_art(entry.get('album_id'), rounded_size)
            if response:
                return response

    flask.abort(404)
