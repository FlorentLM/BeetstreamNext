import sqlite3
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import (
    subsonic_response, subsonic_error,
    map_song, map_album, map_artist,
    sub_to_beets_song, sub_to_beets_album, sub_to_beets_artist,
)


def _set_liked(username: str, item_type: str, item_id: str, liked: bool) -> None:

    db_path = flask.current_app.config['DB_PATH']

    with sqlite3.connect(db_path) as conn:
        if liked:
            conn.execute("""
                INSERT INTO likes (username, item_type, item_id)
                VALUES (?, ?, ?)
                ON CONFLICT (username, item_type, item_id)
                DO UPDATE SET starred_at = unixepoch()
            """, (username, item_type, item_id))
        else:
            conn.execute("""
                DELETE FROM likes
                WHERE username = ? AND item_type = ? AND item_id = ?
            """, (username, item_type, item_id))


@app.route('/rest/star', methods=['GET', 'POST'])
@app.route('/rest/star.view', methods=['GET', 'POST'])
@app.route('/rest/unstar', methods=['GET', 'POST'])
@app.route('/rest/unstar.view', methods=['GET', 'POST'])
def star_or_unstar():
    r = flask.request.values

    resp_fmt = r.get('f', 'xml')
    liked = 'unstar' not in flask.request.path

    song_ids   = r.getlist('id')
    album_ids  = r.getlist('albumId')
    artist_ids = r.getlist('artistId')

    if not any([song_ids, album_ids, artist_ids]):
        return subsonic_error(10, resp_fmt=resp_fmt)

    username = flask.g.username

    for sid in song_ids:
        _set_liked(username, 'song',   sid,  liked)
    for alid in album_ids:
        _set_liked(username, 'album',  alid,  liked)
    for arid in artist_ids:
        _set_liked(username, 'artist', arid, liked)

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
            SELECT item_type, item_id, starred_at FROM likes WHERE username = ?
        """, (username,)).fetchall()

    songs, albums, artists = [], [], []

    for item_type, item_id, starred_at in rows:
        if item_type == 'song':
            item = flask.g.lib.get_item(sub_to_beets_song(item_id))
            if item:
                songs.append(map_song(item))

        elif item_type == 'album':
            item = flask.g.lib.get_album(sub_to_beets_album(item_id))
            if item:
                albums.append(map_album(item, with_songs=False))

        elif item_type == 'artist':
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