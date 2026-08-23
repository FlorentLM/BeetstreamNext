import time
import flask

from .. import api_bp

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.cache import preload_songs
from beetsplug.beetstreamnext.utils.general import timestamp_to_iso
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.api.serializers import IDMapper, map_song, standardise_datadict


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

    current_song_id, position, changed, changed_by = queue_row

    with database() as db:
        entry_rows = db.execute(
            """
            SELECT song_id
            FROM play_queue_entries
            WHERE username = ?
            ORDER BY position
            """, (username,)
        ).fetchall()

    if not entry_rows:
        return subsonic_response({}, resp_fmt=resp_fmt)

    ordered_ids = [r['song_id'] for r in entry_rows]
    resolved = IDMapper.resolve_songs_bulk(ordered_ids)
    items_in_order = [resolved[sid] for sid in ordered_ids if sid in resolved]

    preload_songs(items_in_order)
    songs = [map_song(item) for item in items_in_order]

    payload = {
        'playQueue': {
            'entry': songs,
            'current': current_song_id or '',
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

    resolved = IDMapper.resolve_songs_bulk(song_ids)
    canonical_ids = [IDMapper.mint_song(standardise_datadict(resolved[sid])) for sid in song_ids if sid in resolved]

    current_item = resolved.get(current_sid) if current_sid else None
    if current_sid and current_item is None:
        current_item = IDMapper.resolve_song(current_sid)
    current_canonical = IDMapper.mint_song(standardise_datadict(current_item)) if current_item else None

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
            """, (username, current_canonical, position, now, client)
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
            [(username, i, sid) for i, sid in enumerate(canonical_ids)]
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

    current_song_id, position, changed, changed_by = queue_row

    with database() as db:
        entry_rows = db.execute(
            """
            SELECT song_id
            FROM play_queue_entries
            WHERE username = ?
            ORDER BY position
            """, (username,)
        ).fetchall()

    if not entry_rows:
        return subsonic_response({'playQueueByIndex': {}}, resp_fmt=resp_fmt)

    ordered_ids = [r['song_id'] for r in entry_rows]
    resolved = IDMapper.resolve_songs_bulk(ordered_ids)
    valid_pairs = [(sid, resolved[sid]) for sid in ordered_ids if sid in resolved]

    preload_songs([item for _, item in valid_pairs])

    current_index = 0
    songs = []
    for i, (sid, item) in enumerate(valid_pairs):
        if sid == current_song_id:
            current_index = i
        songs.append(map_song(item))

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

    resolved = IDMapper.resolve_songs_bulk(song_ids)
    canonical_ids = [IDMapper.mint_song(standardise_datadict(resolved[sid])) for sid in song_ids if sid in resolved]

    # And revalidate against parsed list
    if current_index >= len(canonical_ids):
        return subsonic_error(10, message='currentIndex out of bounds.', resp_fmt=resp_fmt)

    current_song_id = canonical_ids[current_index]

    with database() as db:
        db.execute(
            """
            INSERT INTO play_queue (username, current, position, changed, changed_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (username) DO UPDATE SET current    = excluded.current,
                                                 position   = excluded.position,
                                                 changed    = excluded.changed,
                                                 changed_by = excluded.changed_by
            """, (username, current_song_id, position, now, client)
        )

        # Re-populate the entries table
        db.execute("""DELETE FROM play_queue_entries WHERE username = ?""", (username,))
        db.executemany(
            """
            INSERT INTO play_queue_entries (username, position, song_id)
            VALUES (?, ?, ?)
            """,
            [(username, i, sid) for i, sid in enumerate(canonical_ids)]
        )

    return subsonic_response({}, resp_fmt=resp_fmt)