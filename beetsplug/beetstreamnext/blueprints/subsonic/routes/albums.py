from __future__ import annotations
import urllib.parse
import flask

from .. import subsonic_bp

from beetsplug.beetstreamnext.utils.general import page_offset, page_size
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.utils.db import get_beets_schema, chunked_query
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.library.resolve import Resolve
from beetsplug.beetstreamnext.core.library.serialise import Serialise

from beetsplug.beetstreamnext.blueprints.subsonic.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.media.images import tokenised_image_url
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.storage.cache import preload_albums, get_song_counts


def album_payload(subsonic_album_id: str, include_songs: bool = True) -> dict:

    album_object = Resolve.album(subsonic_album_id)
    if not album_object:
        return {}

    payload = {
        "album": {
            **Serialise.album(album_object, include_songs=include_songs)
        }
    }
    return payload


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getAlbum/
@subsonic_bp.route('/getAlbum', methods=['GET', 'POST'])
@subsonic_bp.route('/getAlbum.view', methods=['GET', 'POST'])
def endpoint_get_album() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    album_id = r.get('id', default='', type=safe_str)    # Required

    payload = album_payload(album_id, include_songs=True)
    if not payload:
        return subsonic_error(70, resp_fmt=resp_fmt)

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getAlbumInfo/
@subsonic_bp.route('/getAlbumInfo', methods=['GET', 'POST'])
@subsonic_bp.route('/getAlbumInfo.view', methods=['GET', 'POST'])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getAlbumInfo2/
@subsonic_bp.route('/getAlbumInfo2', methods=['GET', 'POST'])
@subsonic_bp.route('/getAlbumInfo2.view', methods=['GET', 'POST'])
def endpoint_get_album_info() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)      # Required

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    if IDs.decode_type(req_id) == 'song':
        item = Resolve.song(req_id)
        beets_album_id = item.get('album_id') if item else None

        album = flask.g.lib.get_album(beets_album_id) if beets_album_id else None
        image_id = (
            IDs.encode_album(
                beets_album_id,
                album.get('mb_albumid'),
                album.get('albumartist'),
                album.get('album')
            ) if album else req_id
        )

    else:
        album = Resolve.album(req_id)
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
            'smallImageUrl': tokenised_image_url(image_id, size=250),
            'mediumImageUrl': tokenised_image_url(image_id, size=500),
            'largeImageUrl': tokenised_image_url(image_id, size=1200)
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getAlbumList/
@subsonic_bp.route('/getAlbumList', methods=['GET', 'POST'])
@subsonic_bp.route('/getAlbumList.view', methods=['GET', 'POST'])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getAlbumList2/
@subsonic_bp.route('/getAlbumList2', methods=['GET', 'POST'])
@subsonic_bp.route('/getAlbumList2.view', methods=['GET', 'POST'])
def endpoint_get_album_list() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    sort_by = r.get('type', default='alphabeticalByName', type=safe_str)    # Required
    size = r.get('size', default=10, type=page_size)
    offset = r.get('offset', default=0, type=page_offset)
    from_year = r.get('fromYear', default=0, type=int)                      # Required if byYear
    to_year = r.get('toYear', default=3000, type=int)                       # Required if byYear
    genre_filter = r.get('genre', default='', type=safe_str)[:64]           # Required if byGenre

    if not sort_by:
        return subsonic_error(10, message="Sort type is required.", resp_fmt=resp_fmt)

    if sort_by == 'byYear' and (not from_year or not to_year):
        return subsonic_error(10, message="Parameters 'fromYear' and 'to_year' are required to sort by year.", resp_fmt=resp_fmt)

    if sort_by == 'byGenre' and not genre_filter:
        return subsonic_error(10, message="Parameter 'genre' is required to sort by genre.", resp_fmt=resp_fmt)

    tag = 'albumList2' if 'getAlbumList2' in flask.request.path else 'albumList'

    if sort_by in ('starred', 'highest'):

        with database() as db:
            if sort_by == 'starred':
                rows = db.execute(
                    """
                    SELECT item_id
                    FROM likes
                    WHERE username = ? AND item_id LIKE 'al-%'
                    ORDER BY starred_at DESC
                    """, (flask.g.username,)
                ).fetchall()
            else:
                rows = db.execute(
                    """
                    SELECT item_id
                    FROM ratings
                    WHERE username = ? AND item_id LIKE 'al-%'
                    ORDER BY rating DESC, rated_at DESC
                    """, (flask.g.username,)
                ).fetchall()


        albums_list = []
        skipped = 0
        for row in rows:
            album = Resolve.album(row['item_id'])
            if album is None:
                continue
            if skipped < offset:
                skipped += 1
                continue
            albums_list.append(dict(album))
            if len(albums_list) >= size:
                break

    elif sort_by in ('frequent', 'recent'):
        with database() as db:
            rows = db.execute(
                """
                SELECT song_id, play_count, last_played
                FROM play_stats
                WHERE username = ? AND play_count > 0
                """, (flask.g.username,)
            ).fetchall()

        beets_ids = Resolve.song_ids([row['song_id'] for row in rows])
        with flask.g.lib.transaction() as tx:
            album_of = dict(chunked_query(
                tx, 'SELECT id, album_id FROM items WHERE id IN ({q})', list(set(beets_ids.values()))
            ))

        # Play stats per song added per album
        per_album: dict = {}
        for row in rows:
            album_id = album_of.get(beets_ids.get(row['song_id']))
            if not album_id:
                continue
            plays, latest = per_album.get(album_id, (0, 0.0))
            per_album[album_id] = (plays + row['play_count'], max(latest, row['last_played'] or 0.0))

        ranking = 0 if sort_by == 'frequent' else 1
        ranked = sorted(per_album, key=lambda album_id: per_album[album_id][ranking], reverse=True)

        albums_list = []
        for album_id in ranked[offset:]:
            album = flask.g.lib.get_album(album_id)
            if album is not None:
                albums_list.append(dict(album))
            if len(albums_list) >= size:
                break

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

        if sort_by == 'newest':
            query += " ORDER BY added DESC"
        elif sort_by == 'alphabeticalByName':
            query += " ORDER BY album COLLATE NOCASE"
        elif sort_by == 'alphabeticalByArtist':
            query += " ORDER BY albumartist COLLATE NOCASE"
        elif sort_by == 'byYear':
            # Order by year then by month then by day
            sort_dir = 'ASC' if from_year <= to_year else 'DESC'
            query += f" ORDER BY year {sort_dir}, month {sort_dir}, day {sort_dir}"
        elif sort_by == 'random':
            query += " ORDER BY RANDOM()"

        query += " LIMIT ? OFFSET ?"
        params.extend([size, offset])

        with flask.g.lib.transaction() as tx:
            albums = list(tx.query(query, params))

        albums_list = [dict(album) for album in albums]

    song_counts = get_song_counts(albums_list)
    preload_albums(albums_list)

    payload = {
        tag: {
            'album': [Serialise.album(a, include_songs=False, song_counts=song_counts) for a in albums_list]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)