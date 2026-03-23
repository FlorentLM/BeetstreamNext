import sqlite3
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.albums import get_song_counts
from beetsplug.beetstreamnext.db import connect_dual
from beetsplug.beetstreamnext.utils import (
    subsonic_response, subsonic_error,
    map_song, map_album, map_artist,
    sub_to_beets_artist,
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

    with connect_dual() as conn:
        song_rows = conn.execute(
            """
            SELECT i.* 
            FROM likes l
            JOIN beets.items i ON l.item_id = 'sg-' || i.id
            WHERE l.username = ?
            ORDER BY l.starred_at DESC
            """, (username,)
        ).fetchall()

        album_rows = conn.execute(
            """
            SELECT a.* 
            FROM likes l
            JOIN beets.albums a ON l.item_id = 'al-' || a.id
            WHERE l.username = ?
            ORDER BY l.starred_at DESC
            """, (username,)
        ).fetchall()

        artist_rows = conn.execute(
            """
            SELECT item_id 
            FROM likes 
            WHERE username = ? AND item_id LIKE 'ar-%' 
            ORDER BY starred_at DESC
            """, (username,)
        ).fetchall()

    songs = [map_song(dict(row)) for row in song_rows]

    album_dicts = [dict(row) for row in album_rows]
    song_counts = get_song_counts(album_dicts)
    albums = [map_album(row, with_songs=False, song_counts=song_counts) for row in album_dicts]

    artist_ids = [row[0] for row in artist_rows]
    artists = [map_artist(sub_to_beets_artist(aid), with_albums=False) for aid in artist_ids]

    tag = 'starred2' if 'getStarred2' in flask.request.path else 'starred'
    payload = {
        tag: {
            'song':   songs,
            'album':  albums,
            'artist': artists,
        }
    }
    return subsonic_response(payload, resp_fmt)