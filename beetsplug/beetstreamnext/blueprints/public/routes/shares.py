from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
import zipfile
from pathlib import Path
from typing import List, Tuple

import flask
from flask import render_template

from .. import public_bp

from beetsplug.beetstreamnext.constants import ZIP_CACHE_DIR, ZIP_MAX_CONCURRENT
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.notifications import notify
from beetsplug.beetstreamnext.core.runtime.web import external_url, send_file
from beetsplug.beetstreamnext.utils.system import safe_filename
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.library.resolve import Resolve
from beetsplug.beetstreamnext.core.library.serialise import Serialise

from beetsplug.beetstreamnext.core.library.resolve import beets_abspath


def _get_active_share(share_id: str):
    """Fetch a share row, aborting 404 if it doesn't exist or has expired."""
    with database() as db:
        share = db.execute(
            """
            SELECT * FROM shares 
            WHERE id = ?
            """, (share_id,)
        ).fetchone()

    if not share or (share['expires'] and share['expires'] < time.time()):
        flask.abort(404)

    return share


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

    entry_type, item = Resolve.any(entry_id)
    if entry_type != 'song':
        return False

    album_id = item.get('album_id') if item else None
    if not album_id:
        return False

    sub_album_id = IDs.encode_album(album_id, item.get('mb_albumid'), item.get('albumartist'), item.get('album'))
    with database() as db:
        album_match = db.execute(
            """
            SELECT 1 
            FROM share_entries 
            WHERE share_id = ? AND item_id = ?
            """, (share_id, sub_album_id)
        ).fetchone()

    return bool(album_match)


_zip_slots = threading.BoundedSemaphore(ZIP_MAX_CONCURRENT)


def _zip_cache_path(entry_id: str, files: List[Tuple[Path, str]]) -> Path:
    """
    Cached by album and by (name, size, mtime) of the files so repeat downloads cost nothing.
    """

    digest = hashlib.sha256(entry_id.encode())
    for path_obj, arcname in files:
        st = path_obj.stat()
        digest.update(f'\0{path_obj}\0{arcname}\0{st.st_size}\0{st.st_mtime_ns}'.encode())

    return ZIP_CACHE_DIR / f'{digest.hexdigest()}.zip'


def _zip_album(entry_id: str, items: list) -> Path | None:
    """
    Zip an album and returns the (cached) path, or None if too many zips are being built
    """

    files: List[Tuple[Path, str]] = []

    for item in items:
        path_obj = beets_abspath(item)
        if not path_obj.is_file():
            bsn_logger.warning(f"Skipping missing file for album zip: '{path_obj}'")
            continue

        track = item.get('track') or 0
        title = safe_filename(item.get('title') or path_obj.stem, 'untitled')
        arcname = f'{track:02d} - {title}{path_obj.suffix}' if track else f'{title}{path_obj.suffix}'
        files.append((path_obj, arcname))

    zip_path = _zip_cache_path(entry_id, files)
    if zip_path.is_file():
        os.utime(zip_path)      # Recently used: keep
        return zip_path

    if not _zip_slots.acquire(blocking=False):
        return None

    tmp_path = zip_path.with_suffix(f'.{secrets.token_hex(8)}.part')
    try:
        with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_STORED) as zf:
            for path_obj, arcname in files:
                zf.write(path_obj, arcname=arcname)
        os.replace(tmp_path, zip_path)      # Atomic swap to avoid serving half-written zip

    finally:
        tmp_path.unlink(missing_ok=True)
        _zip_slots.release()

    return zip_path


@public_bp.route('/share/<share_id>')
def share_view(share_id: str) -> str | Tuple[str, str]:
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
        return render_template('error.html', error=error), str(410)

    with database() as db:
        db.execute(
            """
            UPDATE shares
            SET visit_count = visit_count + 1
            WHERE id = ?
            """, (share_id,)
        )

    notify('share_accessed', share_owner=share['username'], share_description=share['description'],
           share_url=external_url(flask.url_for('public.share_view', share_id=share_id)),
           visit_count=share['visit_count'] + 1)

    with database() as db:
        entry_rows = db.execute(
            """
            SELECT item_id
            FROM share_entries
            WHERE share_id = ?
            """, (share_id,)
        ).fetchall()

    entry_ids = [r['item_id'] for r in entry_rows]

    resolved_songs, resolved_albums = Resolve.shared_items(entry_ids)
    songs = [Serialise.song(s) for s in resolved_songs]
    albums = [Serialise.album(a, include_songs=True) for a in resolved_albums]

    return render_template('shares.html', share=share, songs=songs, albums=albums)


@public_bp.route('/share/<share_id>/download/<entry_id>')
def share_download(share_id: str, entry_id: str) -> flask.Response | None:

    _get_active_share(share_id)

    if not _is_shared(share_id, entry_id):
        flask.abort(403)

    entry_type, item = Resolve.any(entry_id)
    if entry_type != 'song' or not item:
        flask.abort(404)

    song_path = beets_abspath(item)

    return send_file(
        song_path,
        as_attachment=True,
        download_name=f"{safe_filename(item.title or song_path.stem, 'untitled')}.{(item.format or song_path.suffix.lstrip('.')).lower()}"
    )


@public_bp.route('/share/<share_id>/download-album/<entry_id>')
def share_download_album(share_id: str, entry_id: str) -> flask.Response:

    _get_active_share(share_id)

    entry_type, album = Resolve.any(entry_id)
    if entry_type != 'album':
        flask.abort(404)

    # Albums are only ever shared explicitly (unlike songs which can be valid via
    # their parent album)
    with database() as db:
        explicit_match = db.execute(
            """
            SELECT 1 FROM share_entries 
            WHERE share_id = ? AND item_id = ?
            """, (share_id, entry_id)
        ).fetchone()

    if not explicit_match:
        flask.abort(403)

    if not album:
        flask.abort(404)

    items = sorted(album.items(), key=lambda i: (i.get('disc', 1), i.get('track', 1)))
    if not items:
        flask.abort(404)

    zip_path = _zip_album(entry_id, items)
    if zip_path is None:
        response = flask.make_response('Too many downloads are being prepared, try again shortly.', 503)
        response.headers['Retry-After'] = '10'
        return response

    download_name = f"{safe_filename(album.albumartist, 'untitled')} - {safe_filename(album.album, 'untitled')}.zip"

    return flask.send_file(zip_path, mimetype='application/zip', as_attachment=True, download_name=download_name)


@public_bp.route('/share/<share_id>/cover/<entry_id>')
def share_cover(share_id: str, entry_id: str) -> flask.Response:

    _get_active_share(share_id)

    if not _is_shared(share_id, entry_id):
        flask.abort(403)

    from beetsplug.beetstreamnext.core.media.images import send_album_art, round_image_size

    size = flask.request.args.get('size', default=0, type=int)
    rounded_size = round_image_size(size)

    entry_type, entry = Resolve.any(entry_id)

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
