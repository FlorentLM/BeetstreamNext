import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.albums import get_song_counts
from beetsplug.beetstreamnext.db import database, dual_database
from beetsplug.beetstreamnext.utils import (
    subsonic_response, subsonic_error,
    map_song, map_album, map_artist,
    sub_to_beets_artist, chunked_query, safe_str,
)


def _set_liked(username: str, item_id: str, liked: bool) -> None:

    with database() as db:
        if liked:
            db.execute(
                """
                INSERT INTO likes (username, item_id)
                VALUES (?, ?)
                ON CONFLICT (username, item_id)
                    DO UPDATE SET starred_at = unixepoch()
                """, (username, item_id)
            )
        else:
            db.execute(
                """
                DELETE
                FROM likes
                WHERE username = ?
                  AND item_id = ?
                """, (username, item_id)
            )


# Spec: https://opensubsonic.netlify.app/docs/endpoints/star/
@app.route('/rest/star', methods=['GET', 'POST'])
@app.route('/rest/star.view', methods=['GET', 'POST'])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/unstar/
@app.route('/rest/unstar', methods=['GET', 'POST'])
@app.route('/rest/unstar.view', methods=['GET', 'POST'])
def endpoint_star_or_unstar():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    song_ids = r.getlist('id', type=safe_str)
    album_ids = r.getlist('albumId', type=safe_str)
    artist_ids = r.getlist('artistId', type=safe_str)

    liked = 'unstar' not in flask.request.path

    if not any([song_ids, album_ids, artist_ids]):
        return subsonic_error(10, resp_fmt=resp_fmt)

    username = flask.g.username

    to_like = song_ids + album_ids + artist_ids
    for id_ in to_like:
        _set_liked(username, id_,  liked)

    # TODO: Maybe allow committing to Beets for single user setups?

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getStarred/
@app.route('/rest/getStarred', methods=['GET', 'POST'])
@app.route('/rest/getStarred.view', methods=['GET', 'POST'])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getStarred2/
@app.route('/rest/getStarred2', methods=['GET', 'POST'])
@app.route('/rest/getStarred2.view', methods=['GET', 'POST'])
def endpoint_get_starred():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    username = flask.g.username

    with dual_database() as db:
        song_rows = db.execute(
            """
            SELECT i.* 
            FROM likes l
            JOIN beets.items i ON l.item_id = 'sg-' || i.id
            WHERE l.username = ?
            ORDER BY l.starred_at DESC
            """, (username,)
        ).fetchall()

        album_rows = db.execute(
            """
            SELECT a.* 
            FROM likes l
            JOIN beets.albums a ON l.item_id = 'al-' || a.id
            WHERE l.username = ?
            ORDER BY l.starred_at DESC
            """, (username,)
        ).fetchall()

        artist_rows = db.execute(
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
    albums = [map_album(row, include_songs=False, song_counts=song_counts) for row in album_dicts]

    artist_ids = [row[0] for row in artist_rows]
    beets_artist_names = [sub_to_beets_artist(aid) for aid in artist_ids]

    prefetched = {}
    if beets_artist_names:
        with flask.g.lib.transaction() as tx:
            placeholders = ','.join(['?'] * len(beets_artist_names))
            sql = f"""
                   SELECT albumartist, COUNT(*), mb_albumartistid
                   FROM albums 
                   WHERE albumartist IN ({placeholders}) 
                   GROUP BY albumartist
                   """
            rows = chunked_query(tx, sql, beets_artist_names)
            for r in rows:
                prefetched[r[0]] = {'album_count': r[1], 'mbid': r[2]}

    artists = [map_artist(name, with_albums=False, prefetched=prefetched) for name in beets_artist_names]

    tag = 'starred2' if 'getStarred2' in flask.request.path else 'starred'
    payload = {
        tag: {
            'song':   songs,
            'album':  albums,
            'artist': artists,
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)