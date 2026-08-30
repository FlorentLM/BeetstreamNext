import time
import flask

from .. import api_bp

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.cache import preload_songs
from beetsplug.beetstreamnext.utils.general import timestamp_to_iso
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.mappings import IDs, Resolve, Serialise, standardise_datadict

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getBookmarks/
@api_bp.route('/getBookmarks', methods=['GET', 'POST'])
@api_bp.route('/getBookmarks.view', methods=['GET', 'POST'])
def endpoint_get_bookmarks() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    username = flask.g.username

    with database() as db:
        rows = db.execute(
            """
            SELECT song_id, position, comment, created, changed
            FROM bookmarks
            WHERE username = ?
            """, (username,)
        ).fetchall()

    resolved = Resolve.multiple([row['song_id'] for row in rows])
    preload_songs([obj for entry_type, obj in resolved.values() if entry_type == 'song'])

    bookmarks = []
    for row in rows:
        entry = Serialise.playable(row['song_id'], resolved)
        if not entry:
            continue

        bookmarks.append({
            'entry': entry,
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
@api_bp.route('/createBookmark', methods=['GET', 'POST'])
@api_bp.route('/createBookmark.view', methods=['GET', 'POST'])
def endpoint_create_bookmark() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    req_id = r.get('id', default='', type=safe_str)                 # Required
    position = r.get('position', default=0.0, type=float)           # Required
    comment = r.get('comment', default='', type=safe_str)[:1024]

    if not req_id or position < 0.0:
        return subsonic_error(10, resp_fmt=resp_fmt)

    if IDs.decode_type(req_id) == 'episode':
        episode = Resolve.podcast_episode(req_id)
        if not episode:
            return subsonic_error(70, resp_fmt=resp_fmt)

        canonical_id = IDs.encode_podcast_episode(episode['id'])

    else:
        item = Resolve.song(req_id)
        if not item:
            return subsonic_error(70, resp_fmt=resp_fmt)

        canonical_id = IDs.encode_song(standardise_datadict(item))

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
            """, (username, canonical_id, position, comment, now, now)
            )

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/deleteBookmark/
@api_bp.route('/deleteBookmark', methods=['GET', 'POST'])
@api_bp.route('/deleteBookmark.view', methods=['GET', 'POST'])
def endpoint_delete_bookmark() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)         # Required

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    if IDs.decode_type(req_id) == 'episode':
        episode = Resolve.podcast_episode(req_id)
        canonical_id = IDs.encode_podcast_episode(episode['id']) if episode else None
    else:
        item = Resolve.song(req_id)
        canonical_id = IDs.encode_song(standardise_datadict(item)) if item else None

    username = flask.g.username

    with database() as db:
        db.execute(
            """
            DELETE
            FROM bookmarks
            WHERE username = ? AND song_id = ?
            """, (username, canonical_id)
        )

    return subsonic_response({}, resp_fmt=resp_fmt)