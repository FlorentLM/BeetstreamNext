import sqlite3
import time
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import (
    subsonic_response, sub_to_beets_song, beets_to_sub_song, map_song, timestamp_to_iso
)


@app.route('/rest/getPlayQueue', methods=['GET', 'POST'])
@app.route('/rest/getPlayQueue.view', methods=['GET', 'POST'])
def get_play_queue():
    r = flask.request.values
    resp_fmt = r.get('f', 'xml')
    username = flask.g.username

    db_path = flask.current_app.config['DB_PATH']
    with sqlite3.connect(db_path) as conn:
        queue_row = conn.execute("""
                                 SELECT current, position, changed, changed_by 
                                 FROM play_queue 
                                 WHERE username = ?
                                 """,
            (username,)
        ).fetchone()

        if not queue_row:
            return subsonic_response({}, resp_fmt)

        current_beets_id, position, changed, changed_by = queue_row

        entry_rows = conn.execute("""
                                  SELECT song_id 
                                  FROM play_queue_entries 
                                  WHERE username = ? 
                                  ORDER BY position
                                  """,
            (username,)
        ).fetchall()

    song_ids = [row[0] for row in entry_rows]
    if not song_ids:
        return subsonic_response({}, resp_fmt)

    question_marks = ','.join('?' * len(song_ids))
    with flask.g.lib.transaction() as tx:
        rows = tx.query(f"""SELECT * FROM items WHERE id IN ({question_marks})""", song_ids)

    row_map = {row['id']: row for row in rows}
    songs = [map_song(row_map[sid]) for sid in song_ids if sid in row_map]

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

    db_path = flask.current_app.config['DB_PATH']
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO play_queue (username, current, position, changed, changed_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (username) DO UPDATE SET
                current    = excluded.current,
                position   = excluded.position,
                changed    = excluded.changed,
                changed_by = excluded.changed_by
        """, (username, current_beets_sid, position, now, client))

        conn.execute(
            """DELETE FROM play_queue_entries WHERE username = ?""",
            (username,)
        )

        conn.executemany(
            """INSERT INTO play_queue_entries (username, position, song_id) VALUES (?, ?, ?)""",
            [(username, i, sid) for i, sid in enumerate(song_ids)]
        )

    return subsonic_response({}, resp_fmt)