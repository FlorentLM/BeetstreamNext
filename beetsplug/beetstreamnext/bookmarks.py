import sqlite3
import time
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.db import connect_dual
from beetsplug.beetstreamnext.utils import (
    subsonic_response, subsonic_error,
    sub_to_beets_song, map_song, timestamp_to_iso
)


@app.route('/rest/getBookmarks', methods=['GET', 'POST'])
@app.route('/rest/getBookmarks.view', methods=['GET', 'POST'])
def get_bookmarks():

    r = flask.request.values
    resp_fmt = r.get('f', 'xml')
    username = flask.g.username

    with connect_dual() as conn:
        rows = conn.execute(
            """
            SELECT i.*, b.position, b.comment, b.created, b.changed
            FROM bookmarks b 
                     JOIN beets.items i ON b.song_id = i.id
            WHERE b.username = ?
            """,
            (username,)
        ).fetchall()

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
    return subsonic_response(payload, resp_fmt)


@app.route('/rest/createBookmark', methods=['GET', 'POST'])
@app.route('/rest/createBookmark.view', methods=['GET', 'POST'])
def create_bookmark():
    r = flask.request.values
    resp_fmt = r.get('f', 'xml')

    song_sub_id = r.get('id')
    if not song_sub_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    beets_id = sub_to_beets_song(song_sub_id)
    position = float(r.get('position', 0))
    comment = r.get('comment', '')
    username = flask.g.username
    now = time.time()

    db_path = flask.current_app.config['DB_PATH']
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO bookmarks (username, song_id, position, comment, created, changed) 
            VALUES (?, ?, ?, ?, ?, ?) 
            ON CONFLICT (username, song_id) DO UPDATE SET position = excluded.position,
                                                          comment  = excluded.comment,
                                                          changed  = excluded.changed
            """,
            (username, beets_id, position, comment, now, now)
            )

    return subsonic_response({}, resp_fmt)


@app.route('/rest/deleteBookmark', methods=['GET', 'POST'])
@app.route('/rest/deleteBookmark.view', methods=['GET', 'POST'])
def delete_bookmark():
    r = flask.request.values
    resp_fmt = r.get('f', 'xml')

    song_sub_id = r.get('id')
    if not song_sub_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    beets_id = sub_to_beets_song(song_sub_id)
    username = flask.g.username
    db_path = flask.current_app.config['DB_PATH']

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
                     DELETE 
                     FROM bookmarks 
                     WHERE username = ? AND song_id = ?
                     """,
            (username, beets_id)
        )

    return subsonic_response({}, resp_fmt)