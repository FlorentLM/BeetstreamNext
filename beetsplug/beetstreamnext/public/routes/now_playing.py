import flask

from .. import public_bp

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.api.serializers import IDMapper
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

    item_id = row['item_id']
    id_type = IDMapper.get_type(item_id)

    if id_type == 'song':
        song = IDMapper.resolve_song(item_id)
        if song and song.get('album_id'):
            response = send_album_art(song.get('album_id'), rounded_size)
            if response:
                return response

    elif id_type == 'radio':
        station = IDMapper.resolve_radio(item_id)
        if station:
            response = send_radio_art(station['id'])
            if response:
                return response

    flask.abort(404)
