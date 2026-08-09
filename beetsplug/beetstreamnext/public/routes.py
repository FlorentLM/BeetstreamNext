import os
import time
from pathlib import Path
from typing import Tuple, Any
import flask
from flask import render_template

from . import public_bp

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.utils.general import get_server_info, send_file
from beetsplug.beetstreamnext.api.serializers import IDMapper, map_song, map_album
from beetsplug.beetstreamnext.settings import settings_store

# TODO: It's maybe time to split this file into several route files like the rest api


@public_bp.app_errorhandler(404)
def page_not_found(_e: Any) -> Tuple[str, int]:
    error = {
        'code': 404,
        'title': '*record scratches*',
        'message': "Looks like you're lost.",
    }
    return render_template('error.html', error=error), error['code']

@public_bp.route('/')
def home() -> str:
    stats = get_server_info(extended=False)
    stats['status'] = 'running'

    now_playing = None

    if settings_store.get('public_now_playing'):
        with database() as db:
            row = db.execute(
                """
                SELECT np.item_id, np.player_name, np.username
                FROM now_playing np
                         JOIN users u ON np.username = u.username
                WHERE np.state = 'playing'
                ORDER BY np.started_at DESC
                LIMIT 1
                """
            ).fetchone()

        if row:
            beets_song_id = IDMapper.sub_to_song(row['item_id'])
            song = app.config['lib'].get_item(beets_song_id)
            if song:
                now_playing = {
                    'title': song.title,
                    'artist': song.artist,
                    'album': song.album,
                    'player': row['player_name'],
                    'username': row['username']
                }

    # Resolve the public/external URL
    external_host = settings_store.get('external_hostname')
    if external_host:
        scheme = 'https' if (flask.request.is_secure or settings_store.get('reverse_proxy')) else 'http'
        server_url = f"{scheme}://{external_host}/"
    else:
        server_url = flask.request.host_url

    return render_template(
        'index.html',
        stats=stats,
        now_playing=now_playing,
        server_url=server_url
    )

@public_bp.route('/now-playing/cover')
def now_playing_cover() -> flask.Response:
    if not settings_store.get('public_now_playing'):
        flask.abort(404)

    with database() as db:
        row = db.execute(
            """
            SELECT np.item_id
            FROM now_playing np
                     JOIN users u ON np.username = u.username
            WHERE np.state = 'playing'
            ORDER BY np.started_at DESC
            LIMIT 1
            """
        ).fetchone()

    if not row:
        flask.abort(404)

    from beetsplug.beetstreamnext.core.images import send_album_art, round_image_size
    size = flask.request.args.get('size', default=0, type=int)
    rounded_size = round_image_size(size)

    beets_song_id = IDMapper.sub_to_song(row['item_id'])
    song = app.config['lib'].get_item(beets_song_id)
    if song and song.get('album_id'):
        response = send_album_art(song.get('album_id'), rounded_size)
        if response:
            return response

    flask.abort(404)


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
            item = flask.g.lib.get_item(IDMapper.sub_to_song(entry_id))
            if item:
                songs.append(map_song(item))

        elif entry_type == 'album':
            alb = flask.g.lib.get_album(IDMapper.sub_to_album(entry_id))
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

    if not is_valid and IDMapper.get_type(entry_id) == 'song':

        beets_song_id = IDMapper.sub_to_song(entry_id)
        item = flask.g.lib.get_item(beets_song_id)

        if item:
            album_id = item.get('album_id')

            if album_id:
                sub_album_id = IDMapper.album_to_sub(album_id)

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

    beets_song_id = IDMapper.sub_to_song(entry_id)
    item = flask.g.lib.get_item(beets_song_id)
    if not item:
        flask.abort(404)

    song_path = os.fsdecode(item.get('path', b''))
    path_obj = Path(song_path)
    if not path_obj.is_absolute():
        song_path = str(app.config['root_directory'] / path_obj)

    return send_file(
        song_path,
        as_attachment=True,
        download_name=f"{item.title}.{item.format.lower()}"
    )


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

    if not is_valid and IDMapper.get_type(entry_id) == 'song':

        beets_song_id = IDMapper.sub_to_song(entry_id)
        item = app.config['lib'].get_item(beets_song_id)

        if item:
            album_id = item.get('album_id')
            if album_id:
                sub_album_id = IDMapper.album_to_sub(album_id)

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
        album_id = IDMapper.sub_to_album(entry_id)

        response = send_album_art(album_id, rounded_size)
        if response:
            return response

    elif IDMapper.get_type(entry_id) == 'song':
        beets_song_id = IDMapper.sub_to_song(entry_id)

        item = app.config['lib'].get_item(beets_song_id)

        if item and item.get('album_id'):

            response = send_album_art(item.get('album_id'), rounded_size)
            if response:
                return response

    flask.abort(404)