import time
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.db import database
from beetsplug.beetstreamnext.utils import subsonic_response, subsonic_error, sub_to_beets_song, map_song

# TODO: Lastfm optional integration?

_NOW_PLAYING_TIMEOUT = 600  # 10 min = stale


@app.route('/rest/scrobble', methods=['GET', 'POST'])
@app.route('/rest/scrobble.view', methods=['GET', 'POST'])
def endpoint_scrobble():

    r = flask.request.values
    resp_fmt = r.get('f', 'xml')

    song_ids = r.getlist('id')
    if not song_ids:
        return subsonic_error(10, resp_fmt=resp_fmt)

    # only record if submission=true (does false basically mean 'now playing'?)
    submission = r.get('submission', 'true').lower() != 'false'
    username = flask.g.username
    client = r.get('c') or ''
    now = time.time()

    if not submission:
        # "Now playing" -> only update in-memory store
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
        return subsonic_response({}, resp_fmt)

    times_ms = r.getlist('time')

    with database() as db:
        for i, song_id in enumerate(song_ids):
            beets_id = sub_to_beets_song(song_id)

            try:
                played_at = int(times_ms[i]) / 1000.0
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

    return subsonic_response({}, resp_fmt)


@app.route('/rest/getNowPlaying', methods=['GET', 'POST'])
@app.route('/rest/getNowPlaying.view', methods=['GET', 'POST'])
def endpoint_get_now_playing():
    r = flask.request.values
    resp_fmt = r.get('f', 'xml')

    now = time.time()
    entries = []

    with database() as db:
        db.execute(
            "DELETE FROM now_playing WHERE ? - started_at > ?",
            (now, _NOW_PLAYING_TIMEOUT)
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