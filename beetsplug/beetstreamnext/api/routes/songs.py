from typing import List, Tuple, Dict
import flask

from .. import api_bp

from beetsplug.beetstreamnext.constants import BEETS_MULTI_DELIM
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.external import query_lastfm
from beetsplug.beetstreamnext.core.cache import preload_songs
from beetsplug.beetstreamnext.utils.text import safe_str, validate_mbid
from beetsplug.beetstreamnext.utils.db import get_beets_schema, escape_like
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.mappings import IDs, Resolve, Serialise

def song_payload(subsonic_song_id: str) -> dict:
    song_item = Resolve.song(subsonic_song_id)
    if not song_item:
        return {}

    payload = {
        'song': Serialise.song(song_item)
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
        if field in ('artists', 'composers', 'lyricists'):
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
            # artist / composer / lyricist are single-valued in older beets schema
            conditions.append(f"{field} = ?")
            params.append(name)
    return conditions, params


##
# Endpoints

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getSong/
@api_bp.route('/getSong', methods=['GET', 'POST'])
@api_bp.route('/getSong.view', methods=['GET', 'POST'])
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
@api_bp.route('/getSongsByGenre', methods=['GET', 'POST'])
@api_bp.route('/getSongsByGenre.view', methods=['GET', 'POST'])
def endpoint_songs_by_genre() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    genre = r.get('genre', default='', type=safe_str)[:64]   # Required
    count = r.get('count', default=10, type=int)
    offset = r.get('offset', default=0, type=int)

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
            "song": [Serialise.song(s) for s in songs]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getRandomSongs/
@api_bp.route('/getRandomSongs', methods=['GET', 'POST'])
@api_bp.route('/getRandomSongs.view', methods=['GET', 'POST'])
def endpoint_get_random_songs() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    size = r.get('size', default=10, type=int)
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
            "song": list(map(Serialise.song, songs))
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getTopSongs/
@api_bp.route('/getTopSongs', methods=['GET', 'POST'])
@api_bp.route('/getTopSongs.view', methods=['GET', 'POST'])
def endpoint_get_top_songs() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_artist_name = r.get('artist', default='', type=safe_str)    # Required (unless id is provided)
    req_artist_id = r.get('id', default='', type=safe_str)          # Required (only in OpenSubsonic)
    count = r.get('count', default=50, type=int)

    lookup = req_artist_id if IDs.decode_type(req_artist_id) == 'artist' else req_artist_name
    resolved = Resolve.artist(lookup)
    if not resolved:
        empty_payload = { 'topSongs': { 'song': [] } }
        return subsonic_response(empty_payload, resp_fmt=resp_fmt)

    artist_name, artist_mbid = resolved

    if app.config['lastfm_api_key']:
        if artist_mbid:
            lastfm_resp = query_lastfm(q=artist_mbid, data_type='artist', method='TopTracks', is_mbid=True)
        else:
            lastfm_resp = query_lastfm(q=artist_name, data_type='artist', method='TopTracks', is_mbid=False)

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
        if artist_name in (item.get('albumartist') or '', item.get('artist') or '') \
                or artist_name in (item.get('artists') or ''):
            matches.append(item)
            if len(matches) >= count:
                break

    preload_songs(matches)

    payload = {
        'topSongs': {
            'song': [Serialise.song(song) for song in matches]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


def _similar_by_track(req_id: str, req_artist_name: str, limit: int) -> Dict[int, Dict]:
    """
    Track-level similarity via Last.fm's track.getSimilar, matched against the local library by (artist, title).
    (only possible when the request is for a song (not an album/artist) obviously).
    """
    matches: Dict[int, Dict] = {}

    entry_type, song_item = Resolve.any(req_id)
    if entry_type != 'song' or not song_item or not song_item.get('title'):
        return matches

    song_beets_id = song_item.id

    song_title = song_item.get('title')
    song_mbid = validate_mbid(song_item.get('mb_releasetrackid')) or validate_mbid(song_item.get('mb_trackid'))

    if song_mbid:
        lastfm_resp = query_lastfm(q=song_mbid, data_type='track', method='similar', is_mbid=True)
    else:
        lastfm_resp = query_lastfm(q=song_title, data_type='track', method='similar', is_mbid=False, artist=req_artist_name)

    lastfm_tracks = lastfm_resp.get('similartracks', {}).get('track', [])

    # Safety cap to stay well under SQLite's 999-param limit (2 params per track)
    conditions = []
    params = []
    for t in lastfm_tracks[:min(limit, 400)]:
        t_name = t.get('name')
        t_artist = (t.get('artist') or {}).get('name')
        if not t_name or not t_artist:
            continue
        conditions.append("(lower(artist) = lower(?) AND lower(title) = lower(?))")
        params.extend([t_artist, t_name])

    if not conditions:
        return matches

    query = "SELECT DISTINCT * FROM items WHERE " + " OR ".join(conditions)
    with flask.g.lib.transaction() as tx:
        rows = list(tx.query(query, params))

    for row in rows:
        if row['id'] != song_beets_id:
            matches[row['id']] = row

    return matches


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getSimilarSongs/
@api_bp.route('/getSimilarSongs', methods=['GET', 'POST'])
@api_bp.route('/getSimilarSongs.view', methods=['GET', 'POST'])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getSimilarSongs2/
@api_bp.route('/getSimilarSongs2', methods=['GET', 'POST'])
@api_bp.route('/getSimilarSongs2.view', methods=['GET', 'POST'])
def endpoint_get_similar_songs() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)      # Required
    limit = r.get('count', default=50, type=int)

    if not req_id:
        return subsonic_error(70, resp_fmt=resp_fmt)

    resolved = Resolve.artist(req_id)
    if resolved is None:
        return subsonic_error(70, resp_fmt=resp_fmt)

    req_artist_name, req_artist_mbid = resolved

    matched_songs: Dict[int, Dict] = {}
    if app.config['lastfm_api_key']:
        matched_songs = _similar_by_track(req_id, req_artist_name, limit)

    if len(matched_songs) < limit:
        similar_artists = {}

        if app.config['lastfm_api_key']:
            if req_artist_mbid:
                lastfm_resp = query_lastfm(q=req_artist_mbid, data_type='artist', method='similar', is_mbid=True)
            else:
                lastfm_resp = query_lastfm(q=req_artist_name, data_type='artist', method='similar', is_mbid=False)

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
                name_conds, name_params = _sql_conditions_for(name, name_fields)
                sub_conditions.extend(name_conds)
                params.extend(name_params)

            if sub_conditions:
                conditions.append("(" + " OR ".join(sub_conditions) + ")")

        if conditions:
            # Overfetch a bit since some rows may duplicate what track-level already found
            remaining = limit - len(matched_songs)
            query = "SELECT DISTINCT * FROM items WHERE " + " OR ".join(conditions) + " LIMIT ?"
            params.append(remaining + len(matched_songs))

            with flask.g.lib.transaction() as tx:
                rows = list(tx.query(query, params))

            for row in rows:
                if row['id'] not in matched_songs:
                    matched_songs[row['id']] = row
                if len(matched_songs) >= limit:
                    break

    tag = 'similarSongs2' if 'getSimilarSongs2' in flask.request.path else 'similarSongs'

    final_songs = list(matched_songs.values())[:limit]
    preload_songs(final_songs)

    payload = {
        tag: {
            'song': [Serialise.song(s) for s in final_songs]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)
