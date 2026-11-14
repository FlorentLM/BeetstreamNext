from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flask

from .. import subsonic_bp

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.services.external.lastfm import query_lastfm
from beetsplug.beetstreamnext.core.storage.userprefs_cache import preload_songs
from beetsplug.beetstreamnext.core.library.exclusions import exclusions
from beetsplug.beetstreamnext.utils.general import page_offset, page_size
from beetsplug.beetstreamnext.utils.text import safe_str, split_beets_multi, validate_mbid
from beetsplug.beetstreamnext.utils.db import chunked_query, library_cache, sql_conditions_for
from beetsplug.beetstreamnext.blueprints.subsonic.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.library.indexes import Artist, ArtistIndex, SongIndex
from beetsplug.beetstreamnext.core.library.resolve import Resolve
from beetsplug.beetstreamnext.core.library.serialise import Serialise

def song_payload(subsonic_song_id: str) -> dict:
    song_item = Resolve.song(subsonic_song_id)
    if not song_item:
        return {}

    payload = {
        'song': Serialise.song(song_item, exact_size=True)
    }
    return payload


##
# Endpoints

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getSong/
@subsonic_bp.route('/getSong', methods=['GET', 'POST'])
@subsonic_bp.route('/getSong.view', methods=['GET', 'POST'])
def endpoint_get_song() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    song_id = r.get('id', default='', type=safe_str)     # Required

    if not song_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    payload = song_payload(song_id)
    if not payload:
        return subsonic_error(70, resp_fmt=resp_fmt)

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getSongsByGenre/
@subsonic_bp.route('/getSongsByGenre', methods=['GET', 'POST'])
@subsonic_bp.route('/getSongsByGenre.view', methods=['GET', 'POST'])
def endpoint_songs_by_genre() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    genre = r.get('genre', default='', type=safe_str)[:64]   # Required
    count = r.get('count', default=10, type=page_size)
    offset = r.get('offset', default=0, type=page_offset)

    if not genre:
        return subsonic_error(10, resp_fmt=resp_fmt)

    genre_pattern = f"%{genre}%"

    cols = library_cache.schema('items')
    conditions = []
    params = []

    if 'genres' in cols:
        conditions.append("lower(genres) LIKE lower(?)")
        params.append(genre_pattern)
    if 'genre' in cols:
        conditions.append("lower(genre) LIKE lower(?)")
        params.append(genre_pattern)

    songs = []
    if conditions:

        sql = f"""
        SELECT * FROM items 
        WHERE ({' OR '.join(conditions)}) 
        ORDER BY title LIMIT ? OFFSET ?
        """  # nosec B608  # noqa: S608 vars are constants

        def fetch_songs(limit, skip):
            with flask.g.lib.transaction() as tx:
                return list(tx.query(sql, [*params, limit, skip]))

        exc = exclusions()
        songs = exc.page(fetch_songs, keep=exc.songs, scope='browse', offset=offset, count=count)

    preload_songs(songs)

    payload = {
        "songsByGenre": {
            "song": [Serialise.song(s) for s in songs]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getRandomSongs/
@subsonic_bp.route('/getRandomSongs', methods=['GET', 'POST'])
@subsonic_bp.route('/getRandomSongs.view', methods=['GET', 'POST'])
def endpoint_get_random_songs() -> flask.Response:

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    size = r.get('size', default=10, type=page_size)
    genre = r.get('genre', default='', type=safe_str)[:64]
    from_year = r.get('fromYear', default=0, type=int)
    to_year = r.get('toYear', default=0, type=int)

    conditions = []
    params = []

    if from_year or to_year:
        lo = min(from_year, to_year) if from_year and to_year else (from_year or to_year)
        hi = max(from_year, to_year) if from_year and to_year else 3000
        conditions.append("year BETWEEN ? AND ?")
        params.extend([lo, hi])

    if genre:
        cols = library_cache.schema('items')
        genre_conditions = []
        pattern = f"%{genre.strip().lower()}%"

        if 'genres' in cols:
            genre_conditions.append("lower(genres) LIKE ?")
            params.append(pattern)
        if 'genre' in cols:
            genre_conditions.append("lower(genre) LIKE ?")
            params.append(pattern)
        if genre_conditions:
            conditions.append("(" + " OR ".join(genre_conditions) + ")")

    sql = """SELECT * FROM items"""
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY RANDOM() LIMIT ?"

    songs = []
    seen = set()

    exc = exclusions()

    # Draw more until list is full (or nothing new comes up) to fill the gaps
    for _ in range(5 if exc.active(scope='lists') else 1):

        with flask.g.lib.transaction() as tx:
            drawn = list(tx.query(sql, [*params, size * 2 if exc.active(scope='lists') else size]))

        fresh = [s for s in drawn if s['id'] not in seen]
        seen.update(s['id'] for s in fresh)
        songs.extend(exc.songs(fresh, scope='lists'))

        if len(songs) >= size or not fresh:
            break

    songs = songs[:size]

    preload_songs(songs)

    payload = {
        "randomSongs": {
            "song": list(map(Serialise.song, songs))
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


def _credited_to(artist: Artist) -> Callable[[Any], bool]:
    """Whether a song is credited to this artist (by name, and by MBID if the name is shared)."""

    shared = ArtistIndex.get().shared(artist.name)

    def field(item, key: str) -> str:
        try:
            return item[key] or ''
        except (KeyError, IndexError):    # older beets schema?
            return ''

    def credited(item) -> bool:
        names = (field(item, 'albumartist'), field(item, 'artist'), *split_beets_multi(field(item, 'artists')))
        if artist.name not in names:
            return False
        if not shared:
            return True

        mbids = {validate_mbid(field(item, 'mb_albumartistid')), validate_mbid(field(item, 'mb_artistid')),
                 *(validate_mbid(m) for m in split_beets_multi(field(item, 'mb_artistids')))} - {''}
        return artist.mbid in mbids if artist.mbid else not mbids

    return credited


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getTopSongs/
@subsonic_bp.route('/getTopSongs', methods=['GET', 'POST'])
@subsonic_bp.route('/getTopSongs.view', methods=['GET', 'POST'])
def endpoint_get_top_songs() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_artist_name = r.get('artist', default='', type=safe_str)    # Required (unless id is provided)
    req_artist_id = r.get('id', default='', type=safe_str)          # Required (only in OpenSubsonic)
    count = r.get('count', default=50, type=page_size)

    lookup = req_artist_id if IDs.decode_type(req_artist_id) == 'artist' else req_artist_name
    artist = Resolve.artist(lookup)
    if not artist:
        empty_payload = { 'topSongs': { 'song': [] } }
        return subsonic_response(empty_payload, resp_fmt=resp_fmt)

    credited = _credited_to(artist)

    if app.config['lastfm_api_key']:
        if artist.mbid:
            lastfm_resp = query_lastfm(q=artist.mbid, data_type='artist', method='TopTracks', is_mbid=True)
        else:
            lastfm_resp = query_lastfm(q=artist.name, data_type='artist', method='TopTracks', is_mbid=False)

        lastfm_tracks = lastfm_resp.get('toptracks', {}).get('track', [])
        lastfm_track_names = [t.get('name', '') for t in lastfm_tracks if t.get('name')]

        if lastfm_track_names:
            placeholders = ','.join(['?'] * len(lastfm_track_names))
            columns = library_cache.schema('items')
            name_conditions, name_params = sql_conditions_for(artist.name, [f for f in ('artist', 'artists') if f in columns])
            sql = f"""
                   SELECT * FROM items 
                   WHERE (albumartist = ? OR {' OR '.join(name_conditions)})
                     AND title IN ({placeholders})
                   """  # nosec B608  # noqa: S608 vars are constants
            with flask.g.lib.transaction() as tx:
                top_tracks_available = [s for s in tx.query(sql, [artist.name, *name_params, *lastfm_track_names]) if credited(s)]

            top_tracks_available = exclusions().songs(top_tracks_available, scope='lists')
            if top_tracks_available:
                preload_songs(top_tracks_available)

                payload = {
                    'topSongs': {
                        'song': [Serialise.song(s) for s in top_tracks_available]
                    }
                }
                return subsonic_response(payload, resp_fmt=resp_fmt)

    # Fallback to local play stats
    with database() as db:
        stat_rows = db.execute(
            """
            SELECT song_id, play_count
            FROM play_stats
            WHERE username = ? AND play_count > 0
            ORDER BY play_count DESC
            """, (flask.g.username,)
        ).fetchall()

    resolved = Resolve.songs([r['song_id'] for r in stat_rows])

    matches = []
    for r in stat_rows:
        item = resolved.get(r['song_id'])
        if not item:
            continue
        if credited(item):
            matches.append(item)
            if len(matches) >= count:
                break

    matches = exclusions().songs(matches, scope='lists')
    preload_songs(matches)

    payload = {
        'topSongs': {
            'song': [Serialise.song(song) for song in matches]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


def _similar_by_track(req_id: str, req_artist_name: str, limit: int) -> dict[int, dict]:
    """
    Track-level similarity via Last.fm's track.getSimilar, matched against the local library by (artist, title).
    (only possible when the request is for a song (not an album/artist) obviously).
    """
    entry_type, song_item = Resolve.any(req_id)
    if entry_type != 'song' or not song_item or not song_item.get('title'):
        return {}

    song_beets_id = song_item.id

    song_title = song_item.get('title')
    song_mbid = validate_mbid(song_item.get('mb_releasetrackid')) or validate_mbid(song_item.get('mb_trackid'))

    if song_mbid:
        lastfm_resp = query_lastfm(q=song_mbid, data_type='track', method='similar', is_mbid=True)
    else:
        lastfm_resp = query_lastfm(q=song_title, data_type='track', method='similar', is_mbid=False, artist=req_artist_name)

    lastfm_tracks = lastfm_resp.get('similartracks', {}).get('track', [])

    index = SongIndex.get()
    wanted: dict[int, None] = {}    # Last.fm order (most similar first)

    for t in lastfm_tracks:
        for beets_id in index.named((t.get('artist') or {}).get('name'), t.get('name')):
            if beets_id != song_beets_id:
                wanted[beets_id] = None
        if len(wanted) >= limit:
            break

    with flask.g.lib.transaction() as tx:
        rows = {row['id']: row for row in chunked_query(tx, 'SELECT * FROM items WHERE id IN ({q})', list(wanted))}

    return {beets_id: rows[beets_id] for beets_id in wanted if beets_id in rows}


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getSimilarSongs/
@subsonic_bp.route('/getSimilarSongs', methods=['GET', 'POST'])
@subsonic_bp.route('/getSimilarSongs.view', methods=['GET', 'POST'])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getSimilarSongs2/
@subsonic_bp.route('/getSimilarSongs2', methods=['GET', 'POST'])
@subsonic_bp.route('/getSimilarSongs2.view', methods=['GET', 'POST'])
def endpoint_get_similar_songs() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)      # Required
    limit = r.get('count', default=50, type=page_size)

    if not req_id:
        return subsonic_error(70, resp_fmt=resp_fmt)

    artist = Resolve.artist(req_id)
    if artist is None:
        return subsonic_error(70, resp_fmt=resp_fmt)

    matched_songs: dict[int, dict] = {}
    if app.config['lastfm_api_key']:
        matched_songs = _similar_by_track(req_id, artist.name, limit)

    if len(matched_songs) < limit:
        similar_artists = {}

        if app.config['lastfm_api_key']:
            if artist.mbid:
                lastfm_resp = query_lastfm(q=artist.mbid, data_type='artist', method='similar', is_mbid=True)
            else:
                lastfm_resp = query_lastfm(q=artist.name, data_type='artist', method='similar', is_mbid=False)

            for similar in lastfm_resp.get('similarartists', {}).get('artist', []):
                name = similar.get('name')
                mbid = similar.get('mbid')

                if name and mbid:
                    similar_artists[name] = mbid

        # Always include requested artist
        if artist.name and artist.mbid:
            similar_artists[artist.name] = artist.mbid

        # Filter to columns that actually exist in current beets library
        available_cols = set(library_cache.schema('items'))
        mbid_fields = [f for f in ['mb_artistid', 'mb_artistids'] if f in available_cols]
        name_fields = [f for f in ['artist', 'artists', 'composer', 'composers', 'lyricist', 'lyricists'] if f in available_cols]

        # Safety cap to stay under SQLite 999 param limit
        # (last.fm scores by similarity anyway so the top N are fine)
        if mbid_fields or name_fields:
            name_cost = sum(4 if f == 'artists' else 1 for f in name_fields)
            max_params_per_artist = len(mbid_fields) + name_cost
            max_artists = 998 // max(max_params_per_artist, 1)
            similar_artists = dict(list(similar_artists.items())[:max_artists])

        conditions = []
        params = []

        for name, mbid in similar_artists.items():
            sub_conditions = []

            if mbid:
                # Match the mbid exactly against any mbid field if possible
                for field in mbid_fields:
                    sub_conditions.append(f"{field} = ?")
                    params.append(mbid)

            if name:
                name_conds, name_params = sql_conditions_for(name, name_fields)
                sub_conditions.extend(name_conds)
                params.extend(name_params)

            if sub_conditions:
                conditions.append("(" + " OR ".join(sub_conditions) + ")")

        if conditions:
            # Overfetch a bit since some rows may duplicate what track-level already found
            remaining = limit - len(matched_songs)
            query = "SELECT DISTINCT * FROM items WHERE " + " OR ".join(conditions) + " LIMIT ?"  # nosec B608  # noqa: S608 vars are constants
            params.append(remaining + len(matched_songs))

            with flask.g.lib.transaction() as tx:
                rows = list(tx.query(query, params))

            for row in rows:
                if row['id'] not in matched_songs:
                    matched_songs[row['id']] = row
                if len(matched_songs) >= limit:
                    break

    tag = 'similarSongs2' if 'getSimilarSongs2' in flask.request.path else 'similarSongs'

    final_songs = exclusions().songs(list(matched_songs.values()), scope='lists')[:limit]
    preload_songs(final_songs)

    payload = {
        tag: {
            'song': [Serialise.song(s) for s in final_songs]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)
