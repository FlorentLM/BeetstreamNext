import flask

from .. import public_bp

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.mappings import Resolve

from beetsplug.beetstreamnext.settings import settings_store


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

    from beetsplug.beetstreamnext.core.images import send_album_art, send_radio_art, round_image_size
    size = flask.request.args.get('size', default=0, type=int)
    rounded_size = round_image_size(size)

    entry_type, entry = Resolve.any(row['item_id'])

    if entry_type == 'song':
        if entry and entry.get('album_id'):
            response = send_album_art(entry.get('album_id'), rounded_size)
            if response:
                return response

    elif entry_type == 'radio':
        if entry:
            response = send_radio_art(entry['id'])
            if response:
                return response

    flask.abort(404)
