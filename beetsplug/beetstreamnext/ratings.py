import time
import flask

from beetsplug.beetstreamnext import api_bp
from beetsplug.beetstreamnext.db import database
from beetsplug.beetstreamnext.utils import subsonic_response, subsonic_error, safe_str


# Spec: https://opensubsonic.netlify.app/docs/endpoints/setRating/
@api_bp.route('/setRating', methods=['GET', 'POST'])
@api_bp.route('/setRating.view', methods=['GET', 'POST'])
def endpoint_set_rating() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)      # Required
    rating = r.get('rating', default=0, type=int)   # Required

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

    # TODO: Maybe allow committing to Beets for single user setups?

    return subsonic_response({}, resp_fmt=resp_fmt)