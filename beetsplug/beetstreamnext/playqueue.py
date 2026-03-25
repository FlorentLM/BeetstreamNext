import time
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.db import database, dual_database
from beetsplug.beetstreamnext.utils import (
    subsonic_response, sub_to_beets_song, beets_to_sub_song, map_song, timestamp_to_iso
)


@app.route('/rest/getPlayQueue', methods=['GET', 'POST'])
@app.route('/rest/getPlayQueue.view', methods=['GET', 'POST'])
def get_play_queue():
    r = flask.request.values
    resp_fmt = r.get('f', 'xml')
    username = flask.g.username

    with database() as db:
        queue_row = db.execute(
            """
            SELECT current, position, changed, changed_by 
            FROM play_queue 
            WHERE username = ?
            """, (username,)
        ).fetchone()

        if not queue_row:
            return subsonic_response({}, resp_fmt)

        current_beets_id, position, changed, changed_by = queue_row

        entry_rows = db.execute(
            """
            SELECT song_id 
            FROM play_queue_entries 
            WHERE username = ? 
            ORDER BY position
            """, (username,)
        ).fetchall()

    if not entry_rows:
        return subsonic_response({}, resp_fmt)

    with dual_database() as db:
        rows = db.execute(
            """
            SELECT i.* 
            FROM play_queue_entries pq 
            JOIN beets.items i ON pq.song_id = i.id
            WHERE pq.username = ?
            ORDER BY pq.position
            """, (username,)
        ).fetchall()

    songs = [map_song(dict(row)) for row in rows]

    payload = {
        'playQueue': {
            'entry': songs,
            'current': beets_to_sub_song(current_beets_id) if current_beets_id else '',
            'position': int(position or 0),
            'changed': timestamp_to_iso(changed) if changed else '',
            'changedBy': changed_by or '',
        }
    }
    return subsonic_response(payload, resp_fmt)


@app.route('/rest/savePlayQueue', methods=['GET', 'POST'])
@app.route('/rest/savePlayQueue.view', methods=['GET', 'POST'])
def save_play_queue():
    r = flask.request.values
    resp_fmt = r.get('f', 'xml')
    username = flask.g.username

    song_ids = [sub_to_beets_song(sid) for sid in r.getlist('id') if sid]
    current_sid = r.get('current')
    current_beets_sid = sub_to_beets_song(current_sid) if current_sid else None

    position = float(r.get('position', 0))
    client = r.get('c') or ''
    now = time.time()

    with database() as db:
        db.execute(
            """
            INSERT INTO play_queue (username, current, position, changed, changed_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (username) DO UPDATE SET
                current    = excluded.current,
                position   = excluded.position,
                changed    = excluded.changed,
                changed_by = excluded.changed_by
            """, (username, current_beets_sid, position, now, client)
        )

        db.execute(
            """
            DELETE FROM play_queue_entries 
            WHERE username = ?
            """, (username,)
        )

        db.executemany(
            """
            INSERT INTO play_queue_entries (username, position, song_id) 
            VALUES (?, ?, ?)
            """,
            [(username, i, sid) for i, sid in enumerate(song_ids)]
        )

    return subsonic_response({}, resp_fmt)