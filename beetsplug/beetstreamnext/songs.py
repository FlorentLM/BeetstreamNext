from typing import List, Tuple, Dict

import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.db import dual_database
from beetsplug.beetstreamnext.external import query_lastfm
from beetsplug.beetstreamnext.userdata_caching import preload_songs
from beetsplug.beetstreamnext.utils import (
    subsonic_response, subsonic_error,
    sub_to_beets_song,
    get_beets_schema, safe_str, escape_like
)
from beetsplug.beetstreamnext.constants import ART_ID_PREF, BEETS_MULTI_DELIM
from beetsplug.beetstreamnext.mappings import resolve_artist, map_song


def song_payload(subsonic_song_id: str) -> Dict:
    beets_song_id = sub_to_beets_song(subsonic_song_id)
    song_item = flask.g.lib.get_item(beets_song_id)
    if not song_item:
        return {}

    payload = {
        'song': map_song(song_item)
    }
    return payload


def _sql_conditions_for(name: str, name_fields: List) -> Tuple[List[str], List[str]]:
    """
    Build OR-conditions and params matching `name` across all name columns.
    `artists` is treated as a multi-value beets field with delimiters, everything else is exact-matched.
    """

    conditions = []
    params = []
    escaped = escape_like(name)
    delim = BEETS_MULTI_DELIM

    for field in name_fields:
        if field == 'artists':
            # Four shapes: sole value, first, last, or somewhere in the middle.
            conditions.extend([
                f"{field} = ?",
                f"{field} LIKE ? ESCAPE '!'",
                f"{field} LIKE ? ESCAPE '!'",
                f"{field} LIKE ? ESCAPE '!'",
            ])
            params.extend([
                name,
                f"{escaped}{delim}%",
                f"%{delim}{escaped}",
                f"%{delim}{escaped}{delim}%",
            ])
        else:
            # artist / composer / lyricist are single-valued in beets
            conditions.append(f"{field} = ?")
            params.append(name)
    return conditions, params


##
# Endpoints

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getSong/
@app.route('/rest/getSong', methods=["GET", "POST"])
@app.route('/rest/getSong.view', methods=["GET", "POST"])
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
@app.route('/rest/getSongsByGenre', methods=["GET", "POST"])
@app.route('/rest/getSongsByGenre.view', methods=["GET", "POST"])
def endpoint_songs_by_genre() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    count = r.get('count', default=10, type=int)
    offset = r.get('offset', default=0, type=int)
    genre = r.get('genre', default='', type=safe_str)[:64]   # Required

    if not genre:
        return subsonic_error(10, resp_fmt=resp_fmt)

    genre_pattern = f"%{genre}%"

    cols = get_beets_schema('items')
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
        """
        params.extend([count, offset])

        with flask.g.lib.transaction() as tx:
            songs = list(tx.query(sql, params))

    preload_songs(songs)

    payload = {
        "songsByGenre": {
            "song": [map_song(s) for s in songs]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getRandomSongs/
@app.route('/rest/getRandomSongs', methods=["GET", "POST"])
@app.route('/rest/getRandomSongs.view', methods=["GET", "POST"])
def endpoint_get_random_songs() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    size = r.get('size', default=10, type=int)
    from_year = r.get('fromYear', default=0, type=int)
    to_year = r.get('toYear', default=0, type=int)
    genre = r.get('genre', default='', type=safe_str)[:64]

    conditions = []
    params = []

    if from_year or to_year:
        lo = min(from_year, to_year) if from_year and to_year else (from_year or to_year)
        hi = max(from_year, to_year) if from_year and to_year else 3000
        conditions.append("year BETWEEN ? AND ?")
        params.extend([lo, hi])

    if genre:
        cols = get_beets_schema('items')
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
    params.append(size)

    with flask.g.lib.transaction() as tx:
        songs = list(tx.query(sql, params))

    preload_songs(songs)

    payload = {
        "randomSongs": {
            "song": list(map(map_song, songs))
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getTopSongs/
@app.route('/rest/getTopSongs', methods=["GET", "POST"])
@app.route('/rest/getTopSongs.view', methods=["GET", "POST"])
def endpoint_get_top_songs() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_artist_id = r.get('id', default='', type=safe_str)
    req_artist_name = r.get('artist', default='', type=safe_str)     # Required
    count = r.get('count', default=50, type=int)

    lookup = req_artist_id if req_artist_id.startswith(ART_ID_PREF) else req_artist_name
    resolved = resolve_artist(lookup)
    if not resolved:
        empty_payload = { 'topSongs': { 'song': [] } }
        return subsonic_response(empty_payload, resp_fmt=resp_fmt)

    artist_name, artist_mbid = resolved

    if app.config['lastfm_api_key']:
        if artist_mbid:
            lastfm_resp = query_lastfm(q=artist_mbid, type='artist', method='TopTracks', is_mbid=True)
        else:
            lastfm_resp = query_lastfm(q=artist_name, type='artist', method='TopTracks', is_mbid=False)

        lastfm_tracks = lastfm_resp.get('toptracks', {}).get('track', [])
        lastfm_track_names = [t.get('name', '') for t in lastfm_tracks if t.get('name')]

        if lastfm_track_names:
            placeholders = ','.join(['?'] * len(lastfm_track_names))
            sql = f"""
                   SELECT * FROM items 
                   WHERE (albumartist = ? OR artist = ? OR artists LIKE ?)
                     AND title IN ({placeholders})
                   """
            with flask.g.lib.transaction() as tx:
                top_tracks_available = list(tx.query(sql, [artist_name, artist_name, f"%{artist_name}%"] + lastfm_track_names))

            if top_tracks_available:
                preload_songs(top_tracks_available)

                payload = {
                    'topSongs': {
                        'song': [map_song(s) for s in top_tracks_available]
                    }
                }
                return subsonic_response(payload, resp_fmt=resp_fmt)

    # Fallback to local play stats
    with dual_database() as db:
        rows = db.execute(
            """
            SELECT i.*
            FROM beets.items i
                     JOIN play_stats ps ON ps.song_id = i.id
            WHERE (i.albumartist = ? OR i.artist = ? OR i.artists LIKE ?)
              AND ps.username = ?
              AND ps.play_count > 0
            ORDER BY ps.play_count DESC
            LIMIT ?
            """, (artist_name, artist_name, f"%{artist_name}%", flask.g.username, count)
        ).fetchall()

    preload_songs(rows)

    payload = {
        'topSongs': {
            'song': [map_song(song) for song in rows]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getSimilarSongs/
@app.route('/rest/getSimilarSongs', methods=["GET", "POST"])
@app.route('/rest/getSimilarSongs.view', methods=["GET", "POST"])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getSimilarSongs2/
@app.route('/rest/getSimilarSongs2', methods=["GET", "POST"])
@app.route('/rest/getSimilarSongs2.view', methods=["GET", "POST"])
def endpoint_get_similar_songs() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)      # Required
    limit = r.get('count', default=50, type=int)

    if not req_id:
        return subsonic_error(70, resp_fmt=resp_fmt)

    # TODO - Maybe query the track.getSimilar endpoint on lastfm instead of using the artist?

    resolved = resolve_artist(req_id)
    if resolved is None:
        return subsonic_error(70, resp_fmt=resp_fmt)

    req_artist_name, req_artist_mbid = resolved

    similar_artists = {}

    if app.config['lastfm_api_key']:
        if req_artist_mbid:
            lastfm_resp = query_lastfm(q=req_artist_mbid, type='artist', method='similar', is_mbid=True)
        else:
            lastfm_resp = query_lastfm(q=req_artist_name, type='artist', method='similar', is_mbid=False)

        for artist in lastfm_resp.get('similarartists', {}).get('artist', []):
            name = artist.get('name')
            mbid = artist.get('mbid')

            if name and mbid:
                similar_artists[name] = mbid

    # Always include requested artist
    if req_artist_name and req_artist_mbid:
        similar_artists[req_artist_name] = req_artist_mbid

    # Filter to columns that actually exist in current beets library
    available_cols = set(get_beets_schema('items'))
    mbid_fields = [f for f in ['mb_artistid', 'mb_artistids'] if f in available_cols]
    name_fields = [f for f in ['artist', 'artists', 'composer', 'lyricist'] if f in available_cols]

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
            name_conds, name_params = _sql_conditions_for(name, name_fields)
            sub_conditions.extend(name_conds)
            params.extend(name_params)

        if sub_conditions:
            conditions.append("(" + " OR ".join(sub_conditions) + ")")

    tag = 'similarSongs2' if 'getSimilarSongs2' in flask.request.path else 'similarSongs'

    if not conditions:
        empty_payload = {
            tag: {'song': []}
        }
        return subsonic_response(empty_payload, resp_fmt=resp_fmt)

    query = "SELECT DISTINCT * FROM items WHERE " + " OR ".join(conditions) + " LIMIT ?"
    params.append(limit)

    with flask.g.lib.transaction() as tx:
        avail_similar_songs = list(tx.query(query, params))

    preload_songs(avail_similar_songs)

    payload = {
        tag: {
            'song': [map_song(s) for s in avail_similar_songs]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)
