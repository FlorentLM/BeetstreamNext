import time
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.db import dual_database, database
from beetsplug.beetstreamnext.userdata_caching import preload_songs
from beetsplug.beetstreamnext.utils import (
    subsonic_response, subsonic_error,
    sub_to_beets_song, map_song, timestamp_to_iso, safe_str
)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getBookmarks/
@app.route('/rest/getBookmarks', methods=['GET', 'POST'])
@app.route('/rest/getBookmarks.view', methods=['GET', 'POST'])
def endpoint_get_bookmarks():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    username = flask.g.username

    with dual_database() as db:
        rows = db.execute(
            """
            SELECT i.*, b.position, b.comment, b.created, b.changed
            FROM bookmarks b 
                     JOIN beets.items i ON b.song_id = i.id
            WHERE b.username = ?
            """, (username,)
        ).fetchall()

    preload_songs(rows)

    bookmarks = []
    for row in rows:
        bookmarks.append({
            'entry': map_song(dict(row)),
            'position': int(row['position'] or 0),
            'comment': row['comment'] or '',
            'created': timestamp_to_iso(row['created']) if row['created'] else '',
            'changed': timestamp_to_iso(row['changed']) if row['changed'] else '',
            'username': username,
        })

    payload = {
        'bookmarks': {
            'bookmark': bookmarks
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/createBookmark/
@app.route('/rest/createBookmark', methods=['GET', 'POST'])
@app.route('/rest/createBookmark.view', methods=['GET', 'POST'])
def endpoint_create_bookmark():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    song_id = r.get('id', default='', type=safe_str)             # Required
    position = r.get('position', default=0.0, type=float)       # Required
    comment = r.get('comment', default='', type=safe_str)[:1024]

    if not song_id or position < 0.0:
        return subsonic_error(10, resp_fmt=resp_fmt)

    beets_id = sub_to_beets_song(song_id)
    username = flask.g.username
    now = time.time()

    with database() as db:
        db.execute(
            """
            INSERT INTO bookmarks (username, song_id, position, comment, created, changed) 
            VALUES (?, ?, ?, ?, ?, ?) 
            ON CONFLICT (username, song_id) DO UPDATE SET position = excluded.position,
                                                          comment  = excluded.comment,
                                                          changed  = excluded.changed
            """, (username, beets_id, position, comment, now, now)
            )

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/deleteBookmark/
@app.route('/rest/deleteBookmark', methods=['GET', 'POST'])
@app.route('/rest/deleteBookmark.view', methods=['GET', 'POST'])
def endpoint_delete_bookmark():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    song_id = r.get('id', default='', type=safe_str)     # Required

    if not song_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    beets_id = sub_to_beets_song(song_id)
    username = flask.g.username

    with database() as db:
        db.execute(
            """
            DELETE 
            FROM bookmarks 
            WHERE username = ? AND song_id = ?
            """, (username, beets_id)
        )

    return subsonic_response({}, resp_fmt=resp_fmt)