import sqlite3
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import (
    subsonic_response, subsonic_error,
    map_song, map_album, map_artist,
    sub_to_beets_song, sub_to_beets_album, sub_to_beets_artist, SNG_ID_PREF, ALB_ID_PREF, ART_ID_PREF
)


def _set_liked(username: str, item_id: str, liked: bool) -> None:

    db_path = flask.current_app.config['DB_PATH']

    with sqlite3.connect(db_path) as conn:
        if liked:
            conn.execute("""
                         INSERT INTO likes (username, item_id)
                         VALUES (?, ?)
                         ON CONFLICT (username, item_id)
                             DO UPDATE SET starred_at = unixepoch()
                         """, (username, item_id))
        else:
            conn.execute("""
                         DELETE
                         FROM likes
                         WHERE username = ?
                           AND item_id = ?
                         """, (username, item_id))

@app.route('/rest/star', methods=['GET', 'POST'])
@app.route('/rest/star.view', methods=['GET', 'POST'])
@app.route('/rest/unstar', methods=['GET', 'POST'])
@app.route('/rest/unstar.view', methods=['GET', 'POST'])
def star_or_unstar():
    r = flask.request.values

    resp_fmt = r.get('f', 'xml')
    liked = 'unstar' not in flask.request.path

    song_ids = r.getlist('id')
    album_ids = r.getlist('albumId')
    artist_ids = r.getlist('artistId')

    if not any([song_ids, album_ids, artist_ids]):
        return subsonic_error(10, resp_fmt=resp_fmt)

    username = flask.g.username

    to_like = song_ids + album_ids + artist_ids
    for id_ in to_like:
        _set_liked(username, id_,  liked)

    return subsonic_response({}, resp_fmt)


@app.route('/rest/getStarred', methods=['GET', 'POST'])
@app.route('/rest/getStarred.view', methods=['GET', 'POST'])
@app.route('/rest/getStarred2', methods=['GET', 'POST'])
@app.route('/rest/getStarred2.view', methods=['GET', 'POST'])
def get_starred():
    r = flask.request.values
    resp_fmt = r.get('f', 'xml')
    username = flask.g.username

    db_path = flask.current_app.config['DB_PATH']

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("""
            SELECT item_id, starred_at FROM likes WHERE username = ?
        """, (username,)).fetchall()

    songs, albums, artists = [], [], []

    for item_id, starred_at in rows:
        if item_id.startswith(SNG_ID_PREF):
            item = flask.g.lib.get_item(sub_to_beets_song(item_id))
            if item:
                songs.append(map_song(item))

        elif item_id.startswith(ALB_ID_PREF):
            item = flask.g.lib.get_album(sub_to_beets_album(item_id))
            if item:
                albums.append(map_album(item, with_songs=False))

        elif item_id.startswith(ART_ID_PREF):
            artist_name = sub_to_beets_artist(item_id)
            artists.append(map_artist(artist_name, with_albums=False))

    tag = 'starred2' if flask.request.path.rsplit('.', 1)[0].endswith('2') else 'starred'
    payload = {
        tag: {
            'song':   songs,
            'album':  albums,
            'artist': artists,
        }
    }
    return subsonic_response(payload, resp_fmt)