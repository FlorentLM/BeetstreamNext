from typing import List, Dict
import urllib.parse
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.db import dual_database
from beetsplug.beetstreamnext.utils import (
    get_beets_schema, sub_to_beets_album, map_album, subsonic_response, chunked_query, imageart_url
)


def album_payload(subsonic_album_id: str, with_songs=True) -> dict:

    beets_album_id = sub_to_beets_album(subsonic_album_id)
    album_object = flask.g.lib.get_album(beets_album_id)

    payload = {
        "album": {
            **map_album(album_object, with_songs=with_songs)
        }
    }
    return payload


def get_song_counts(albums: List[Dict]) -> Dict:
    """Get song counts for a list of albums in a single db query."""

    album_ids = [row['id'] for row in albums]

    if album_ids:
        with (flask.g.lib.transaction() as tx):
            sql_query = ('SELECT album_id, COUNT(*) as count, CAST(SUM(length) AS INTEGER) as duration'
                         + ' FROM items WHERE album_id IN ({q}) GROUP BY album_id')
            count_rows = chunked_query(
                tx,
                sql_query,
                album_ids
            )
        counts = {row['album_id']: (row['count'], row['duration'] or 0) for row in count_rows}
    else:
        counts = {}

    return counts


@app.route('/rest/getAlbum', methods=["GET", "POST"])
@app.route('/rest/getAlbum.view', methods=["GET", "POST"])
def endpoint_get_album():
    r = flask.request.values
    album_id = r.get('id')
    payload = album_payload(album_id, with_songs=True)
    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getAlbumInfo', methods=["GET", "POST"])
@app.route('/rest/getAlbumInfo.view', methods=["GET", "POST"])

@app.route('/rest/getAlbumInfo2', methods=["GET", "POST"])
@app.route('/rest/getAlbumInfo2.view', methods=["GET", "POST"])
def endpoint_get_album_info(ver=None):
    r = flask.request.values

    req_id = r.get('id')
    album_id = sub_to_beets_album(req_id)
    album = flask.g.lib.get_album(album_id)

    artist_quot = urllib.parse.quote(album.get('albumartist', ''))
    album_quot = urllib.parse.quote(album.get('album', ''))
    lastfm_url = f'https://www.last.fm/music/{artist_quot}/{album_quot}' if artist_quot and album_quot else ''

    tag = 'albumInfo2' if 'getAlbumInfo2' in flask.request.path else 'albumInfo'
    payload = {
        tag: {
        'musicBrainzId': album.get('mb_albumid', ''),
        'lastFmUrl': lastfm_url,
        'smallImageUrl': imageart_url(req_id, size=250),
        'mediumImageUrl': imageart_url(req_id, size=500),
        'largeImageUrl': imageart_url(req_id, size=1200)
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getAlbumList', methods=["GET", "POST"])
@app.route('/rest/getAlbumList.view', methods=["GET", "POST"])

@app.route('/rest/getAlbumList2', methods=["GET", "POST"])
@app.route('/rest/getAlbumList2.view', methods=["GET", "POST"])
def endpoint_get_album_list(ver=None):

    r = flask.request.values

    sort_by = r.get('type', 'alphabeticalByName')
    size = int(r.get('size', 10))
    offset = int(r.get('offset', 0))
    from_year = int(r.get('fromYear', 0))
    to_year = int(r.get('toYear', 3000))
    genre_filter = (r.get('genre') or '')[:64] or None

    tag = 'albumList2' if 'getAlbumList2' in flask.request.path else 'albumList'

    if sort_by in ('starred', 'frequent', 'highest'):
        with dual_database() as db:

            if sort_by == 'starred':
                album_rows = db.execute(
                    """
                    SELECT a.*
                    FROM likes l
                             JOIN beets.albums a ON l.item_id = 'al-' || a.id
                    WHERE l.username = ?
                    ORDER BY l.starred_at DESC
                    LIMIT ? OFFSET ?
                    """, (flask.g.username, size, offset)
                ).fetchall()

            elif sort_by == 'frequent':
                album_rows = db.execute(
                    """
                    SELECT a.*, SUM(ps.play_count) as total_plays
                    FROM play_stats ps
                             JOIN beets.items i ON ps.song_id = i.id
                             JOIN beets.albums a ON i.album_id = a.id
                    WHERE ps.username = ?
                    GROUP BY a.id
                    ORDER BY total_plays DESC
                    LIMIT ? OFFSET ?
                    """, (flask.g.username, size, offset)
                ).fetchall()

            elif sort_by == 'highest':
                album_rows = db.execute(
                    """
                    SELECT a.*
                    FROM ratings r
                             JOIN beets.albums a ON r.item_id = 'al-' || a.id
                    WHERE r.username = ?
                    ORDER BY r.rating DESC
                    LIMIT ? OFFSET ?
                    """, (flask.g.username, size, offset)
                ).fetchall()

        album_dicts = [dict(row) for row in album_rows]
        counts = get_song_counts(album_dicts)

        payload = {
            tag: {
                "album": [map_album(a, with_songs=False, song_counts=counts) for a in album_dicts]
            }
        }
        return subsonic_response(payload, r.get('f', 'xml'))

    # All other sort types we can do in SQL directly
    query = """SELECT * FROM albums"""
    conditions = []
    params = []

    # filtering conditions:
    if sort_by == 'byYear':
        conditions.append("year BETWEEN ? AND ?")
        params.extend([min(from_year, to_year), max(from_year, to_year)])

    if sort_by == 'byGenre' and genre_filter:
        cols = get_beets_schema('albums')
        genre_conditions = []
        pattern = f"%{genre_filter.strip().lower()}%"

        if 'genres' in cols:
            genre_conditions.append("lower(genres) LIKE ?")
            params.append(pattern)
        if 'genre' in cols:
            genre_conditions.append("lower(genre) LIKE ?")
            params.append(pattern)
        if genre_conditions:
            conditions.append("(" + " OR ".join(genre_conditions) + ")")
        else:
            conditions.append("1 = 0")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    # ordering based on sort_by parameter
    if sort_by == 'newest':
        query += " ORDER BY added DESC"
    elif sort_by == 'alphabeticalByName':
        query += " ORDER BY album COLLATE NOCASE"
    elif sort_by == 'alphabeticalByArtist':
        query += " ORDER BY albumartist COLLATE NOCASE"
    elif sort_by == 'recent':
        query += " ORDER BY year DESC"
    elif sort_by == 'byYear':
        # Order by year, then by month and day
        sort_dir = 'ASC' if from_year <= to_year else 'DESC'
        query += f" ORDER BY year {sort_dir}, month {sort_dir}, day {sort_dir}"
    elif sort_by == 'random':
        query += " ORDER BY RANDOM()"

    # LIMIT and OFFSET for pagination
    query += " LIMIT ? OFFSET ?"
    params.extend([size, offset])

    with flask.g.lib.transaction() as tx:
        albums = list(tx.query(query, params))

    song_counts = get_song_counts(albums)
    payload = {
        tag: {
            "album": [map_album(a, with_songs=False, song_counts=song_counts) for a in albums]
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))