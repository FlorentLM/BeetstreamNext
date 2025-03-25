from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app, stream
import flask


def song_payload(subsonic_song_id: str) -> dict:
    beets_song_id = stb_song(subsonic_song_id)
    song_item = flask.g.lib.get_item(beets_song_id)

    payload = {
        'song': map_song(song_item)
    }
    return payload


@app.route('/rest/getSong', methods=["GET", "POST"])
@app.route('/rest/getSong.view', methods=["GET", "POST"])
def get_song():
    r = flask.request.values
    song_id = r.get('id')

    payload = song_payload(song_id)
    return subsonic_response(payload, r.get('f', 'xml'))

@app.route('/rest/getSongsByGenre', methods=["GET", "POST"])
@app.route('/rest/getSongsByGenre.view', methods=["GET", "POST"])
def songs_by_genre():
    r = flask.request.values

    genre = r.get('genre').replace("'", "\\'")
    count = int(r.get('count') or 10)
    offset = int(r.get('offset') or 0)

    genre_pattern = f"%{genre}%"
    with flask.g.lib.transaction() as tx:
        songs = list(tx.query(
            "SELECT * FROM items WHERE lower(genre) LIKE lower(?) ORDER BY title LIMIT ? OFFSET ?",
            (genre_pattern, count, offset)
        ))

    payload = {
        "songsByGenre": {
            "song": list(map(map_song, songs))
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getRandomSongs', methods=["GET", "POST"])
@app.route('/rest/getRandomSongs.view', methods=["GET", "POST"])
def get_random_songs():
    r = flask.request.values

    size = int(r.get('size') or 10)

    with flask.g.lib.transaction() as tx:
        # Advance the SQL random generator state
        _ = list(tx.query("SELECT RANDOM()"))
        # Now fetch the random songs
        songs = list(tx.query(
            "SELECT * FROM items ORDER BY RANDOM() LIMIT ?",
            (size,)
        ))

    payload = {
        "randomSongs": {
            "song": list(map(map_song, songs))
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/stream', methods=["GET", "POST"])
@app.route('/rest/stream.view', methods=["GET", "POST"])
def stream_song():
    r = flask.request.values

    max_bitrate = int(r.get('maxBitRate') or 0)
    req_format = r.get('format')

    song_id = stb_song(r.get('id'))
    item = flask.g.lib.get_item(song_id)

    item_path = item.get('path', b'').decode('utf-8') if item else ''
    if not item_path:
        flask.abort(404)

    if app.config['never_transcode'] or req_format == 'raw' or max_bitrate <= 0 or item.bitrate <= max_bitrate * 1000:
        return stream.direct(item_path)
    else:
        return stream.try_transcode(item_path, max_bitrate)

@app.route('/rest/download', methods=["GET", "POST"])
@app.route('/rest/download.view', methods=["GET", "POST"])
def download_song():
    r = flask.request.values

    song_id = stb_song(r.get('id'))
    item = flask.g.lib.get_item(song_id)

    return stream.direct(item.path.decode('utf-8'))


@app.route('/rest/getTopSongs', methods=["GET", "POST"])
@app.route('/rest/getTopSongs.view', methods=["GET", "POST"])
def get_top_songs():
    # TODO - Use the play_count, and/or link with Last.fm or ListenBrainz

    r = flask.request.values

    payload = {
        'topSongs': {}
    }
    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getStarred', methods=["GET", "POST"])
@app.route('/rest/getStarred.view', methods=["GET", "POST"])
def get_starred_songs():
    return _starred_songs()

@app.route('/rest/getStarred2', methods=["GET", "POST"])
@app.route('/rest/getStarred2.view', methods=["GET", "POST"])
def get_starred2_songs():
    return _starred_songs(ver=2)


def _starred_songs(ver=None):
    # TODO

    r = flask.request.values

    tag = f'starred{ver if ver else ''}'
    payload = {
        tag: {
            'song': []
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))
