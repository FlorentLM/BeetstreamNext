import time
import flask

from beetsplug.beetstreamnext import app, _now_playing, _now_playing_lock
from beetsplug.beetstreamnext.db import database
from beetsplug.beetstreamnext.utils import subsonic_response, subsonic_error, sub_to_beets_song, map_song

# TODO: Lastfm optional integration?

_NOW_PLAYING_TIMEOUT = 600  # 10 min = stale


@app.route('/rest/scrobble', methods=['GET', 'POST'])
@app.route('/rest/scrobble.view', methods=['GET', 'POST'])
def scrobble():

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
        with _now_playing_lock:
            _now_playing[username] = {
                'song_id': beets_id,
                'started_at': now,
                'player_name': client,
            }
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
def get_now_playing():
    r = flask.request.values
    resp_fmt = r.get('f', 'xml')

    now = time.time()
    entries = []

    with _now_playing_lock:
        stale = [u for u, v in _now_playing.items() if now - v['started_at'] > _NOW_PLAYING_TIMEOUT]
        for u in stale:
            del _now_playing[u]
        snapshot = list(_now_playing.items())

    for username, info in snapshot:
        item = flask.g.lib.get_item(info['song_id'])
        if not item:
            continue

        entry = map_song(item)
        entry['username'] = username
        entry['minutesAgo'] = int((now - info['started_at']) / 60)
        entry['playerName'] = info['player_name']
        entries.append(entry)

    return subsonic_response({'nowPlaying': {'entry': entries}}, resp_fmt)