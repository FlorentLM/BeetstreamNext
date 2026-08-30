import time
import flask

from .. import api_bp

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.settings import settings_store
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.beets_interaction import commit_likes


# Spec: https://opensubsonic.netlify.app/docs/endpoints/setRating/
@api_bp.route('/setRating', methods=['GET', 'POST'])
@api_bp.route('/setRating.view', methods=['GET', 'POST'])
def endpoint_set_rating() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)         # Required
    rating = r.get('rating', default=0, type=int)           # Required

    if not flask.g.user_data.get('commentRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    if not (0 <= rating <= 5):
        return subsonic_error(10, resp_fmt=resp_fmt)

    username = flask.g.username

    with database() as db:
        if rating == 0:
            db.execute(
                """
                DELETE FROM ratings 
                WHERE username = ? AND item_id = ?
                """, (username, req_id)
            )
        else:
            db.execute(
                """
                INSERT INTO ratings (username, item_id, rating, rated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (username, item_id) DO UPDATE SET
                                                              rating   = excluded.rating,
                                                              rated_at = excluded.rated_at
                """, (username, req_id, rating, time.time())
            )

    if username and username == settings_store.get('ratings_writeback_user'):
        commit_likes(req_id, 'rating', rating)

    return subsonic_response({}, resp_fmt=resp_fmt)