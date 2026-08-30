from flask import render_template

from .. import public_bp

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.mappings import Resolve

from beetsplug.beetstreamnext.settings import settings_store
from beetsplug.beetstreamnext.utils.general import get_server_info, external_url


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
            entry_type, entry = Resolve.any(row['item_id'])

            if entry_type == 'song' and entry:
                now_playing = {
                    'title': entry.title,
                    'artist': entry.artist,
                    'album': entry.album,
                    'player': row['player_name'],
                    'username': row['username']
                }

            elif entry_type == 'radio' and entry:
                now_playing = {
                    'title': entry['name'],
                    'artist': 'Internet Radio',
                    'album': '',
                    'player': row['player_name'],
                    'username': row['username']
                }

    server_url = external_url('/')

    return render_template(
        'index.html',
        stats=stats,
        now_playing=now_playing,
        server_url=server_url
    )
