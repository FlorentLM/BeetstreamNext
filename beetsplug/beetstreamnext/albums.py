from typing import List, Dict

import flask
import urllib.parse
from functools import partial

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import (
    get_beets_schema, sub_to_beets_album, map_album, subsonic_response, ALB_ID_PREF
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

@app.route('/rest/getAlbum', methods=["GET", "POST"])
@app.route('/rest/getAlbum.view', methods=["GET", "POST"])
def get_album():
    r = flask.request.values
    album_id = r.get('id')
    payload = album_payload(album_id, with_songs=True)
    return subsonic_response(payload, r.get('f', 'xml'))

@app.route('/rest/getAlbumInfo', methods=["GET", "POST"])
@app.route('/rest/getAlbumInfo.view', methods=["GET", "POST"])

@app.route('/rest/getAlbumInfo2', methods=["GET", "POST"])
@app.route('/rest/getAlbumInfo2.view', methods=["GET", "POST"])
def get_album_info(ver=None):
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
        'largeImageUrl': flask.url_for('get_cover_art', id=album_id, size=1200, _external=False),
        'mediumImageUrl': flask.url_for('get_cover_art', id=album_id, size=500, _external=False),
        'smallImageUrl': flask.url_for('get_cover_art', id=album_id, size=250, _external=False)
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))

@app.route('/rest/getAlbumList', methods=["GET", "POST"])
@app.route('/rest/getAlbumList.view', methods=["GET", "POST"])

@app.route('/rest/getAlbumList2', methods=["GET", "POST"])
@app.route('/rest/getAlbumList2.view', methods=["GET", "POST"])
def get_album_list(ver=None):

    r = flask.request.values

    sort_by = r.get('type', 'alphabeticalByName')
    size = int(r.get('size', 10))
    offset = int(r.get('offset', 0))
    from_year = int(r.get('fromYear', 0))
    to_year = int(r.get('toYear', 3000))
    genre_filter = r.get('genre')

    tag = 'albumList2' if 'getAlbumList2' in flask.request.path else 'albumList'

    # Starred filter needs data from the two dbs
    if sort_by == 'starred':
        starred = {
            sub_to_beets_album(item_id): starred_at
            for item_id, starred_at in flask.g.liked.items()
            if item_id.startswith(ALB_ID_PREF)
        }

        # Sort by starred_at descending then page
        sorted_ids = sorted(starred, key=lambda aid: starred[aid], reverse=True)
        paged_ids = sorted_ids[offset:offset + size]

        if paged_ids:
            placeholders = ','.join('?' * len(paged_ids))

            with flask.g.lib.transaction() as tx:
                rows = tx.query(f"SELECT * FROM albums WHERE id IN ({placeholders})", paged_ids)

            row_map = {row['id']: row for row in rows}
            albums = [row_map[aid] for aid in paged_ids if aid in row_map]
        else:
            albums = []

        payload = {tag: {"album": list(map(partial(map_album, with_songs=False), albums))}}
        return subsonic_response(payload, r.get('f', 'xml'))

    # All other sort types we can do in SQL directly
    query = "SELECT * FROM albums"
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

    # TODO - sort_by: highest, frequent

    # Add LIMIT and OFFSET for pagination
    query += " LIMIT ? OFFSET ?"
    params.extend([size, offset])

    with flask.g.lib.transaction() as tx:
        albums = tx.query(query, params)

    payload = {
        tag: {
            "album": list(map(partial(map_album, with_songs=False), albums))
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))