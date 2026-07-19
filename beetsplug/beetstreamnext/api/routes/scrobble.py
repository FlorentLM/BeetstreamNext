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
                'playerId': 0,      # this is a required field
                'positionMs': row['position_ms'],
                'state': row['state'],
                'playbackRate': row['playback_rate']
            })
            entries.append(entry)

    payload = {
        'nowPlaying': {
            'entry': entries
        }
    }

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/reportplayback/
@api_bp.route('/reportPlayback', methods=['GET', 'POST'])
@api_bp.route('/reportPlayback.view', methods=['GET', 'POST'])
def endpoint_report_playback() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    media_id = r.get('mediaId', default='', type=safe_str)          # Required
    media_type = r.get('mediaType', default='song', type=safe_str)
    position_ms = r.get('positionMs', default=0, type=int)          # Required
    state = r.get('state', default='playing', type=safe_str)        # Required
    playback_rate = r.get('playbackRate', default=1.0, type=float)
    ignore_scrobble = r.get('ignoreScrobble', default=False, type=api_bool)
    client = r.get('c', default='', type=safe_str)

    # TODO: media_type can be podcast once podcassts are supported by BSN

    if not media_id or not state:
        return subsonic_error(10, resp_fmt=resp_fmt)

    username = flask.g.username
    now = time.time()

    # Update "now playing" state
    with database() as db:
        # starting, so reset 'scrobbled' flag for this session
        if state == 'starting':
            db.execute(
                """
                INSERT INTO now_playing (username, item_id, started_at, player_name, position_ms, state, playback_rate,
                                         scrobbled)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT (username) DO UPDATE SET item_id       = excluded.item_id,
                                                     started_at    = excluded.started_at,
                                                     player_name   = excluded.player_name,
                                                     position_ms   = excluded.position_ms,
                                                     state         = excluded.state,
                                                     playback_rate = excluded.playback_rate,
                                                     scrobbled     = 0
                """, (username, media_id, now, client, position_ms, state, playback_rate)
            )
        else:
            db.execute(
                """
                UPDATE now_playing
                SET item_id       = ?,
                    position_ms   = ?,
                    state         = ?,
                    playback_rate = ?,
                    player_name   = ?
                WHERE username = ?
                """, (media_id, position_ms, state, playback_rate, client, username)
            )

    # Check for scrobble threshold: played for 4 minutes or 50% of total length
    if not ignore_scrobble and state == 'playing' and IDMapper.get_type(media_id) == 'song':
        beets_id = IDMapper.sub_to_song(media_id)
        item = flask.g.lib.get_item(beets_id)

        if item:
            duration_ms = (item.get('length') or 0) * 1000
            threshold = min(4 * 60 * 1000, duration_ms * 0.5)

            if position_ms >= threshold:
                # Check if already scrobbled this session
                with database() as db:
                    session = db.execute(
                        """
                        SELECT scrobbled 
                        FROM now_playing 
                        WHERE username = ?
                        """, (username,)
                    ).fetchone()

                    if session and not session['scrobbled']:
                        # Update play stats
                        db.execute(
                            """
                            INSERT INTO play_stats (username, song_id, play_count, last_played)
                            VALUES (?, ?, 1, ?)
                            ON CONFLICT (username, song_id)
                                DO UPDATE SET play_count  = play_count + 1,
                                              last_played = excluded.last_played
                            """, (username, beets_id, now)
                        )
                        # Mark as scrobbled to not count again until the next 'starting' state
                        db.execute(
                            """
                            UPDATE now_playing 
                            SET scrobbled = 1 
                            WHERE username = ?
                            """, (username,)
                        )

                        if app.config.get('lastfm_api_key') and flask.g.user_data.get('scrobblingEnabled'):
                            # TODO: Last.fm scrobble call
                            pass

    return subsonic_response({}, resp_fmt=resp_fmt)