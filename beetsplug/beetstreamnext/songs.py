import re
import flask

from beets.dbcore.query import MatchQuery

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.db import dual_database
from beetsplug.beetstreamnext.utils import (
    subsonic_response, subsonic_error,
    ART_ID_PREF, ALB_ID_PREF, SNG_ID_PREF,
    sub_to_beets_artist, sub_to_beets_album, sub_to_beets_song,
    map_song, query_lastfm, get_beets_schema
)


artists_separators = re.compile(r', | & ')


def song_payload(subsonic_song_id: str) -> dict:
    beets_song_id = sub_to_beets_song(subsonic_song_id)
    song_item = flask.g.lib.get_item(beets_song_id)

    payload = {
        'song': map_song(song_item)
    }
    return payload


##
# Endpoints

@app.route('/rest/getSong', methods=["GET", "POST"])
@app.route('/rest/getSong.view', methods=["GET", "POST"])
def endpoint_get_song():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    song_id = r.get('id', default='', type=str)

    if not song_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    payload = song_payload(song_id)
    return subsonic_response(payload, resp_fmt=resp_fmt)


@app.route('/rest/getSongsByGenre', methods=["GET", "POST"])
@app.route('/rest/getSongsByGenre.view', methods=["GET", "POST"])
def endpoint_songs_by_genre():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    count = r.get('count', default=10, type=int)
    offset = r.get('offset', default=0, type=int)
    genre = r.get('genre', default='', type=str)[:64]

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

    payload = {
        "songsByGenre": {
            "song": [map_song(s) for s in songs]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


@app.route('/rest/getRandomSongs', methods=["GET", "POST"])
@app.route('/rest/getRandomSongs.view', methods=["GET", "POST"])
def endpoint_get_random_songs():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    size = r.get('size', default=10, type=int)

    with flask.g.lib.transaction() as tx:
        songs = list(tx.query(
            """
            SELECT * 
            FROM items 
            ORDER BY RANDOM() 
            LIMIT ?""", (size,)
        ))

    payload = {
        "randomSongs": {
            "song": list(map(map_song, songs))
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


@app.route('/rest/getTopSongs', methods=["GET", "POST"])
@app.route('/rest/getTopSongs.view', methods=["GET", "POST"])
def endpoint_get_top_songs():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    req_artist_id = r.get('id', default='', type=str)
    req_artist_name = r.get('artist', default='', type=str)
    count = r.get('count', default=50, type=int)

    if req_artist_id and req_artist_id.startswith(ART_ID_PREF):
        artist_name = sub_to_beets_artist(req_artist_id)
    else:
        artist_name = req_artist_name

    # grab the artist's mbid
    with flask.g.lib.transaction() as tx:
        mbid_artist = tx.query(
            """
            SELECT mb_artistid 
            FROM items 
            WHERE albumartist LIKE ? 
            LIMIT 1
            """, (artist_name,)
        )

    if app.config['lastfm_api_key']:
        # Query last.fm for top tracks for this artist and parse the response
        if mbid_artist:
            lastfm_resp = query_lastfm(q=mbid_artist[0][0], type='artist', method='TopTracks', mbid=True)
        else:
            lastfm_resp = query_lastfm(q=artist_name, type='artist', method='TopTracks', mbid=False)

        if lastfm_resp:
            top_tracks_available = []

            for t in lastfm_resp.get('toptracks', {}).get('track', []):
                query = MatchQuery('title', t.get('name', ''))
                beets_results = list(flask.g.lib.items(query))
                if beets_results:
                    top_tracks_available.append(beets_results[0])

            if top_tracks_available:
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
            WHERE i.albumartist = ? AND ps.username = ? AND ps.play_count > 0
            ORDER BY ps.play_count DESC 
            LIMIT ?
            """, (artist_name, flask.g.username, count)
        ).fetchall()

    payload = {
        'topSongs': {
            'song': [map_song(song) for song in rows]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


@app.route('/rest/getSimilarSongs', methods=["GET", "POST"])
@app.route('/rest/getSimilarSongs.view', methods=["GET", "POST"])

@app.route('/rest/getSimilarSongs2', methods=["GET", "POST"])
@app.route('/rest/getSimilarSongs2.view', methods=["GET", "POST"])
def endpoint_get_similar_songs():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    req_id = r.get('id', default='', type=str)
    limit = r.get('count', default=50, type=int)

    if req_id.startswith(SNG_ID_PREF):
        # TODO - Maybe query the track.getSimilar endpoint on lastfm instead of using the artist?
        beets_song_id = sub_to_beets_song(req_id)
        song_item = flask.g.lib.get_item(beets_song_id)

        if not song_item:
            return subsonic_error(70, resp_fmt=resp_fmt)

        req_artist_name = song_item.get('albumartist', '')
        req_artist_mbid = song_item.get('mb_artistid', '')

    elif req_id.startswith(ALB_ID_PREF):
        beets_album_id = sub_to_beets_album(req_id)
        album_object = flask.g.lib.get_album(beets_album_id)

        if not album_object:
            return subsonic_error(70, resp_fmt=resp_fmt)

        req_artist_name = album_object.get('albumartist', '')
        req_artist_mbid = album_object.get('mb_artistid', '')

    else:
        req_artist_name = sub_to_beets_artist(req_id) if req_id.startswith(ART_ID_PREF) else req_id

        with flask.g.lib.transaction() as tx:
            beets_artist_mbid = tx.query(
                """
                SELECT mb_artistid 
                FROM items 
                WHERE albumartist LIKE ? 
                LIMIT 1
                """, (req_artist_name,)
            )
        try:
            req_artist_mbid = beets_artist_mbid[0][0]
        except IndexError:
            return subsonic_error(70, resp_fmt=resp_fmt)

    similar_artists = {}

    if app.config['lastfm_api_key']:
        # Query last.fm for similar artists and parse the response
        if req_artist_mbid:
            lastfm_resp = query_lastfm(q=req_artist_mbid, type='artist', method='similar', mbid=True)
        else:
            lastfm_resp = query_lastfm(q=req_artist_name, type='artist', method='similar', mbid=False)

        lastfm_artists = lastfm_resp.get('similarartists', {}).get('artist', [])
        for artist in lastfm_artists:
            artist_name = artist.get('name')
            artist_mbid = artist.get('mbid')

            if artist_name and artist_mbid:
                similar_artists[artist_name] = artist_mbid

    # Add also the requested artist (will be the only fallback if no lastfm key available)
    if req_artist_name and req_artist_mbid:
        similar_artists[req_artist_name] = req_artist_mbid

    # Build up a humongous SQL query to get everything with related artists
    mbid_fields = ['mb_artistid', 'mb_artistids']
    name_fields = ['artist', 'artists', 'composer', 'lyricist']
    conditions = []
    params = []
    for name, mbid in similar_artists.items():
        if mbid:
            # When we have an mbid, match against all relevant mbid fields
            sub_conditions = []

            # Check each mbid field for an exact match
            for field in mbid_fields:
                sub_conditions.append(f"{field} = ?")
                params.append(mbid)

            # Also check each name field with a LIKE condition
            for field in name_fields:
                sub_conditions.append(f"{field} LIKE ?")
                params.append(f"%{name}%")
            conditions.append("(" + " OR ".join(sub_conditions) + ")")

        else:
            # no mbid: typically with lastfm responses that's bc the entry is several artists in a collab
            parts = re.split(artists_separators, name)
            sub_conditions_outer = []
            for part in parts:
                sub_conditions_inner = []
                for field in name_fields:
                    sub_conditions_inner.append(f"{field} LIKE ?")
                    params.append(f"%{part}%")
                sub_conditions_outer.append("(" + " OR ".join(sub_conditions_inner) + ")")
            conditions.append("(" + " OR ".join(sub_conditions_outer) + ")")

    query = "SELECT DISTINCT * FROM items WHERE " + " OR ".join(conditions) + " LIMIT ?"
    params.append(limit)

    with flask.g.lib.transaction() as tx:
        avail_similar_songs = list(tx.query(query, params))

    tag = 'similarSongs2' if 'getSimilarSongs2' in flask.request.path else 'similarSongs'
    payload = {
        tag: {
            'song': [map_song(s) for s in avail_similar_songs]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)
