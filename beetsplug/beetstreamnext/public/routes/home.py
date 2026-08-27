import flask
from flask import render_template

from .. import public_bp

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.api.serializers import IDMapper
from beetsplug.beetstreamnext.settings import settings_store
from beetsplug.beetstreamnext.utils.general import get_server_info


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
            item_id = row['item_id']
            id_type = IDMapper.get_type(item_id)

            if id_type == 'song':
                song = IDMapper.resolve_song(item_id)
                if song:
                    now_playing = {
                        'title': song.title,
                        'artist': song.artist,
                        'album': song.album,
                        'player': row['player_name'],
                        'username': row['username']
                    }

            elif id_type == 'radio':
                station = IDMapper.resolve_radio(item_id)
                if station:
                    now_playing = {
                        'title': station['name'],
                        'artist': 'Internet Radio',
                        'album': '',
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
