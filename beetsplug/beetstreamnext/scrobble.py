import time
import flask

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import NOW_PLAYING_TIMEOUT_SEC
from beetsplug.beetstreamnext.db import database
from beetsplug.beetstreamnext.utils import (
    subsonic_response, subsonic_error, sub_to_beets_song, api_bool, safe_str
)
from beetsplug.beetstreamnext.mappings import map_song


# Spec: https://opensubsonic.netlify.app/docs/endpoints/scrobble/
@app.route('/rest/scrobble', methods=['GET', 'POST'])
@app.route('/rest/scrobble.view', methods=['GET', 'POST'])
def endpoint_scrobble() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    submission = r.get('submission', default=True, type=api_bool)
    client = r.get('c', default='', type=safe_str)
    song_ids = r.getlist('id', type=safe_str)        # Required
    times_ms = r.getlist('time', type=int)

    if not song_ids:
        return subsonic_error(10, resp_fmt=resp_fmt)

    username = flask.g.username
    now = time.time()

    if not submission:
        # if not submission it's just a "Now playing"
        beets_id = sub_to_beets_song(song_ids[0])
        with database() as db:
            db.execute(
                """
                INSERT INTO now_playing (username, song_id, started_at, player_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (username) DO UPDATE SET
                    song_id     = excluded.song_id,
                    started_at  = excluded.started_at,
                    player_name = excluded.player_name
                """, (username, beets_id, now, client)
            )
        return subsonic_response({}, resp_fmt=resp_fmt)

    with database() as db:
        for i, song_id in enumerate(song_ids):
            beets_id = sub_to_beets_song(song_id)

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
        # TODO: Lastfm scrobble (optional)
        pass

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getNowPlaying/
@app.route('/rest/getNowPlaying', methods=['GET', 'POST'])
@app.route('/rest/getNowPlaying.view', methods=['GET', 'POST'])
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
            SELECT username, song_id, started_at, player_name 
            FROM now_playing
            """
        ).fetchall()

    for row in rows:
        username, song_id, started_at, player_name = row
        item = flask.g.lib.get_item(song_id)
        if not item:
            continue

        entry = map_song(item)
        entry['username'] = username
        entry['minutesAgo'] = int((now - started_at) / 60)
        entry['playerName'] = player_name
        entry['playerId'] = 0   # this is a required field
        entries.append(entry)

    payload = {
        'nowPlaying': {
            'entry': entries
        }
    }

    return subsonic_response(payload, resp_fmt=resp_fmt)