import sqlite3
import time
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import subsonic_response, subsonic_error, sub_to_beets_song


# TODO: Lastfm optional integration?


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
    if not submission:
        return subsonic_response({}, resp_fmt)

    times_ms = r.getlist('time')
    username = flask.g.username
    now = time.time()

    db_path = flask.current_app.config['DB_PATH']

    with sqlite3.connect(db_path) as conn:
        for i, song_id in enumerate(song_ids):
            beets_id = sub_to_beets_song(song_id)

            try:
                played_at = int(times_ms[i]) / 1000.0
            except (IndexError, ValueError):
                played_at = now

            conn.execute(
                """
                INSERT INTO play_stats (username, song_id, play_count, last_played)
                VALUES (?, ?, 1, ?)
                ON CONFLICT (username, song_id)
                DO UPDATE SET
                    play_count  = play_count + 1,
                    last_played = excluded.last_played
                """,
                (username, beets_id, played_at)
            )
    return subsonic_response({}, resp_fmt)