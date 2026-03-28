import time
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.db import database
from beetsplug.beetstreamnext.utils import subsonic_response, subsonic_error


# Spec: https://opensubsonic.netlify.app/docs/endpoints/setRating/
@app.route('/rest/setRating', methods=['GET', 'POST'])
@app.route('/rest/setRating.view', methods=['GET', 'POST'])
def endpoint_set_rating():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    req_id = r.get('id', default='', type=str)
    rating = r.get('rating', default=0, type=int)

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    if rating not in range(0, 6):
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