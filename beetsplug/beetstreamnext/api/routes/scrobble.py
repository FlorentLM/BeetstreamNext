import time
import flask

from .. import api_bp

from beetsplug.beetstreamnext.constants import NOW_PLAYING_TIMEOUT_SEC
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.api.serializers import IDMapper, map_song


# Spec: https://opensubsonic.netlify.app/docs/endpoints/scrobble/
@api_bp.route('/scrobble', methods=['GET', 'POST'])
@api_bp.route('/scrobble.view', methods=['GET', 'POST'])
def endpoint_scrobble() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    submission = r.get('submission', default=True, type=api_bool)
    client = r.get('c', default='', type=safe_str)
    playing_ids = r.getlist('id', type=safe_str)        # Required
    times_ms = r.getlist('time', type=int)

    if not playing_ids:
        return subsonic_error(10, resp_fmt=resp_fmt)

    username = flask.g.username
    now = time.time()

    if not submission:
        # if not submission it's just a "Now playing"
        p_id = playing_ids[0]

        with database() as db:
            db.execute(
                """
                INSERT INTO now_playing (username, item_id, started_at, player_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (username) DO UPDATE SET
                    item_id     = excluded.item_id,
                    started_at  = excluded.started_at,
                    player_name = excluded.player_name
                """, (username, p_id, now, client)
            )
        return subsonic_response({}, resp_fmt=resp_fmt)

    with database() as db:
        for i, p_id in enumerate(playing_ids):

            if IDMapper.get_type(p_id) == 'song':       # only keep count of songs played, not radios
                beets_id = IDMapper.sub_to_song(p_id)

                try:
                    played_at = times_ms[i] / 1000.0
                except (IndexError, ValueError):
                    played_at = now

                db.execute(
                    """
                    INSERT INTO play_stats (username, song_id, play_count, last_played)
                    VALUES (?, ?, 1, ?)
                    ON CONFLICT (username, song_id)
                    DO UPDATE SET
                        play_count  = play_count + 1,
                        last_played = excluded.last_played
                    """, (username, beets_id, played_at)
                )

    if app.config.get('lastfm_api_key') and flask.g.user_data.get('scrobblingEnabled'):
        # TODO: Lastfm scrobble
        pass

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getNowPlaying/
@api_bp.route('/getNowPlaying', methods=['GET', 'POST'])
@api_bp.route('/getNowPlaying.view', methods=['GET', 'POST'])
def endpoint_get_now_playing() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    now = time.time()
    entries = []

    with database() as db:
        db.execute(
            """
            DELETE FROM now_playing WHERE ? - started_at > ?
            """, (now, NOW_PLAYING_TIMEOUT_SEC)
        )
        rows = db.execute(
            """
            SELECT username, item_id, started_at, player_name 
            FROM now_playing
            """
        ).fetchall()

    for row in rows:
        p_id = row['item_id']
        entry = None

        if p_id.startswith('sg-'):
            beets_id = IDMapper.sub_to_song(p_id)
            item = flask.g.lib.get_item(beets_id)
            if item:
                entry = map_song(item)

        # elif item_id.startswith('ir-'):    # TODO: Now playing radio

        if entry:
            entry.update({
                'username': row['username'],
                'minutesAgo': int((time.time() - row['started_at']) / 60),
                'playerName': row['player_name'],
                'playerId': 0   # this is a required field
            })
            entries.append(entry)

    payload = {
        'nowPlaying': {
            'entry': entries
        }
    }

    return subsonic_response(payload, resp_fmt=resp_fmt)