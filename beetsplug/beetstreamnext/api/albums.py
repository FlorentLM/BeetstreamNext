import urllib.parse
from typing import Dict

import flask

from . import api_bp

from beetsplug.beetstreamnext.constants import SNG_ID_PREF
from beetsplug.beetstreamnext.db import dual_database
from beetsplug.beetstreamnext.utils import (
    get_beets_schema, subsonic_response, subsonic_error,
    safe_str, beets_to_sub_album, sub_to_beets_album, sub_to_beets_song
)
from beetsplug.beetstreamnext.images import image_url
from beetsplug.beetstreamnext.mappings import map_album, get_song_counts
from beetsplug.beetstreamnext.userdata_caching import preload_albums


def album_payload(subsonic_album_id: str, include_songs: bool = True) -> Dict:

    beets_album_id = sub_to_beets_album(subsonic_album_id)
    album_object = flask.g.lib.get_album(beets_album_id)
    if not album_object:
        return {}

    payload = {
        "album": {
            **map_album(album_object, include_songs=include_songs)
        }
    }
    return payload


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getAlbum/
@api_bp.route('/getAlbum', methods=["GET", "POST"])
@api_bp.route('/getAlbum.view', methods=["GET", "POST"])
def endpoint_get_album() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    album_id = r.get('id', default='', type=safe_str)    # Required

    payload = album_payload(album_id, include_songs=True)
    if not payload:
        return subsonic_error(70, resp_fmt=resp_fmt)

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getAlbumInfo/
@api_bp.route('/getAlbumInfo', methods=["GET", "POST"])
@api_bp.route('/getAlbumInfo.view', methods=["GET", "POST"])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getAlbumInfo2/
@api_bp.route('/getAlbumInfo2', methods=["GET", "POST"])
@api_bp.route('/getAlbumInfo2.view', methods=["GET", "POST"])
def endpoint_get_album_info() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)      # Required

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    if req_id.startswith(SNG_ID_PREF):
        item = flask.g.lib.get_item(sub_to_beets_song(req_id))
        beets_album_id = item.get('album_id') if item else None

        album = flask.g.lib.get_album(beets_album_id) if beets_album_id else None
        image_id = beets_to_sub_album(beets_album_id) if beets_album_id else req_id

    else:
        album = flask.g.lib.get_album(sub_to_beets_album(req_id))
        image_id = req_id

    if not album:
        return subsonic_error(70, resp_fmt=resp_fmt)

    artist_quot = urllib.parse.quote(album.get('albumartist', ''))
    album_quot = urllib.parse.quote(album.get('album', ''))
    lastfm_url = f'https://www.last.fm/music/{artist_quot}/{album_quot}' if artist_quot and album_quot else ''

    tag = 'albumInfo2' if 'getAlbumInfo2' in flask.request.path else 'albumInfo'
    payload = {
        tag: {
            'musicBrainzId': album.get('mb_albumid', ''),
            'lastFmUrl': lastfm_url,
            'smallImageUrl': image_url(image_id, size=250),
            'mediumImageUrl': image_url(image_id, size=500),
            'largeImageUrl': image_url(image_id, size=1200)
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getAlbumList/
@api_bp.route('/getAlbumList', methods=["GET", "POST"])
@api_bp.route('/getAlbumList.view', methods=["GET", "POST"])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getAlbumList2/
@api_bp.route('/getAlbumList2', methods=["GET", "POST"])
@api_bp.route('/getAlbumList2.view', methods=["GET", "POST"])
def endpoint_get_album_list() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    sort_by = r.get('type', default='alphabeticalByName', type=safe_str)     # Required
    size = r.get('size', default=10, type=int)
    offset = r.get('offset', default=0, type=int)
    from_year = r.get('fromYear', default=0, type=int)          # Required if byYear
    to_year = r.get('toYear', default=3000, type=int)           # Required if byYear
    genre_filter = r.get('genre', default='', type=safe_str)[:64]    # Required if byGenre

    if not sort_by:
        return subsonic_error(10, message="Sort type is required.", resp_fmt=resp_fmt)

    if sort_by == 'byYear' and (not from_year or not to_year):
        return subsonic_error(10, message="Parameters 'fromYear' and 'to_year' are required to sort by year.", resp_fmt=resp_fmt)

    if sort_by == 'byGenre' and not genre_filter:
        return subsonic_error(10, message="Parameter 'genre' is required to sort by genre.", resp_fmt=resp_fmt)

    tag = 'albumList2' if 'getAlbumList2' in flask.request.path else 'albumList'

    if sort_by in ('starred', 'frequent', 'highest', 'recent'):
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

            elif sort_by == 'recent':
                album_rows = db.execute(
                    """
                    SELECT a.*, MAX(ps.last_played) as latest_play
                    FROM play_stats ps
                             JOIN beets.items i ON ps.song_id = i.id
                             JOIN beets.albums a ON i.album_id = a.id
                    WHERE ps.username = ?
                    GROUP BY a.id
                    ORDER BY latest_play DESC
                    LIMIT ? OFFSET ?
                    """, (flask.g.username, size, offset)
                ).fetchall()

        albums_dict = [dict(row) for row in album_rows]

    else:
        # All other sort types we can do in SQL directly
        query = """SELECT * FROM albums"""
        conditions = []
        params = []

        # filtering conditions:
        if sort_by == 'byYear':
            conditions.append("year BETWEEN ? AND ?")
            params.extend([min(from_year, to_year), max(from_year, to_year)])

        if sort_by == 'byGenre':
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

        albums_dict = [dict(album) for album in albums]

    song_counts = get_song_counts(albums_dict)
    preload_albums(albums_dict)

    payload = {
        tag: {
            "album": [map_album(a, include_songs=False, song_counts=song_counts) for a in albums_dict]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)