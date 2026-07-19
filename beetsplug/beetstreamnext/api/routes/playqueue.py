import time
import flask

from .. import api_bp

from beetsplug.beetstreamnext.core.database import database, dual_database
from beetsplug.beetstreamnext.core.cache import preload_songs
from beetsplug.beetstreamnext.utils.general import timestamp_to_iso
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.api.serializers import IDMapper, map_song


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getPlayQueue/
@api_bp.route('/getPlayQueue', methods=['GET', 'POST'])
@api_bp.route('/getPlayQueue.view', methods=['GET', 'POST'])
def endpoint_get_play_queue() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

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
        return subsonic_response({}, resp_fmt=resp_fmt)

    current_beets_id, position, changed, changed_by = queue_row

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

    if not rows:
        return subsonic_response({}, resp_fmt=resp_fmt)

    preload_songs(rows)
    songs = [map_song(dict(row)) for row in rows]

    payload = {
        'playQueue': {
            'entry': songs,
            'current': IDMapper.song_to_sub(current_beets_id) if current_beets_id else '',
            'position': int(position or 0),
            'changed': timestamp_to_iso(changed) if changed else '',
            'changedBy': changed_by or '',
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/savePlayQueue/
@api_bp.route('/savePlayQueue', methods=['GET', 'POST'])
@api_bp.route('/savePlayQueue.view', methods=['GET', 'POST'])
def endpoint_save_play_queue() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    client = r.get('c', default='', type=safe_str)
    position = r.get('position', default=0.0, type=float)
    current_sid = r.get('current', default='', type=safe_str)    # Required unless id is empty
    song_ids = r.getlist('id', type=safe_str)

    username = flask.g.username

    beets_song_ids = [IDMapper.sub_to_song(sid) for sid in song_ids if sid]
    current_beets_sid = IDMapper.sub_to_song(current_sid) if current_sid else None

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
            [(username, i, sid) for i, sid in enumerate(beets_song_ids)]
        )

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getPlayQueueByIndex/
@api_bp.route('/getPlayQueueByIndex', methods=['GET', 'POST'])
@api_bp.route('/getPlayQueueByIndex.view', methods=['GET', 'POST'])
def endpoint_get_play_queue_by_index() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
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
        return subsonic_response({'playQueueByIndex': {}}, resp_fmt=resp_fmt)

    current_beets_id, position, changed, changed_by = queue_row

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

    if not rows:
        return subsonic_response({'playQueueByIndex': {}}, resp_fmt=resp_fmt)

    preload_songs(rows)

    current_index = 0
    songs = []
    for i, row in enumerate(rows):
        if row['id'] == current_beets_id:
            current_index = i
        songs.append(map_song(dict(row)))

    payload = {
        'playQueueByIndex': {
            'currentIndex': current_index,
            'position': int(position or 0),
            'username': username,
            'changed': timestamp_to_iso(changed) if changed else '',
            'changedBy': changed_by or '',
            'entry': songs,
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/savePlayQueueByIndex/
@api_bp.route('/savePlayQueueByIndex', methods=['GET', 'POST'])
@api_bp.route('/savePlayQueueByIndex.view', methods=['GET', 'POST'])
def endpoint_save_play_queue_by_index() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    client = r.get('c', default='', type=safe_str)

    song_ids = r.getlist('id', type=safe_str)
    position = r.get('position', default=0, type=int)
    username = flask.g.username
    now = time.time()

    # Clear queue: Spec says "Send a call without any parameters to clear the currently saved queue.
    # In this case, currentIndex must not be set."
    if not song_ids:
        if 'currentIndex' in r:
            return subsonic_error(10, message='currentIndex should not be set when clearing the queue.',
                                  resp_fmt=resp_fmt)

        with database() as db:
            db.execute("""DELETE FROM play_queue WHERE username = ?""", (username,))
            db.execute("""DELETE FROM play_queue_entries WHERE username = ?""", (username,))
        return subsonic_response({}, resp_fmt=resp_fmt)

    # Spec says "currentIndex is required unless no id is provided."
    if 'currentIndex' not in r:
        return subsonic_error(10, message="currentIndex is required.", resp_fmt=resp_fmt)

    current_index = r.get('currentIndex', type=int)

    # Spec says "If currentIndex is not between 0 and length of the queue - 1 (inclusive),
    # the server *must* respond with error code 10."
    if current_index < 0 or current_index >= len(song_ids):
        return subsonic_error(10, message='currentIndex out of bounds.', resp_fmt=resp_fmt)

    beets_song_ids = [IDMapper.sub_to_song(sid) for sid in song_ids if sid]
    current_beets_id = beets_song_ids[current_index]

    with database() as db:
        db.execute(
            """
            INSERT INTO play_queue (username, current, position, changed, changed_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (username) DO UPDATE SET current    = excluded.current,
                                                 position   = excluded.position,
                                                 changed    = excluded.changed,
                                                 changed_by = excluded.changed_by
            """, (username, current_beets_id, position, now, client)
        )

        # Re-populate the entries table
        db.execute("""DELETE FROM play_queue_entries WHERE username = ?""", (username,))
        db.executemany(
            """
            INSERT INTO play_queue_entries (username, position, song_id)
            VALUES (?, ?, ?)
            """,
            [(username, i, sid) for i, sid in enumerate(beets_song_ids)]
        )

    return subsonic_response({}, resp_fmt=resp_fmt)