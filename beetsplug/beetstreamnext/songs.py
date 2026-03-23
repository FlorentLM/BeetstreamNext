import os
import re
import flask

from beets.dbcore.query import MatchQuery

from beetsplug.beetstreamnext import app, stream
from beetsplug.beetstreamnext.db import connect_dual
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


@app.route('/rest/getSong', methods=["GET", "POST"])
@app.route('/rest/getSong.view', methods=["GET", "POST"])
def get_song():
    r = flask.request.values
    song_id = r.get('id')

    if not song_id:
        return subsonic_error(10, resp_fmt=r.get('f', 'xml'))

    payload = song_payload(song_id)
    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getSongsByGenre', methods=["GET", "POST"])
@app.route('/rest/getSongsByGenre.view', methods=["GET", "POST"])
def songs_by_genre():
    r = flask.request.values

    count = int(r.get('count') or 10)
    offset = int(r.get('offset') or 0)

    genre = r.get('genre')
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
        sql = f"SELECT * FROM items WHERE ({' OR '.join(conditions)}) ORDER BY title LIMIT ? OFFSET ?"
        params.extend([count, offset])

        with flask.g.lib.transaction() as tx:
            songs = list(tx.query(sql, params))

    payload = {
        "songsByGenre": {
            "song": list(map(map_song, songs))
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getRandomSongs', methods=["GET", "POST"])
@app.route('/rest/getRandomSongs.view', methods=["GET", "POST"])
def get_random_songs():
    r = flask.request.values

    size = int(r.get('size') or 10)

    with flask.g.lib.transaction() as tx:
        # Advance the SQL random generator state
        _ = list(tx.query("SELECT RANDOM()"))
        # Now fetch the random songs
        songs = list(tx.query(
            "SELECT * FROM items ORDER BY RANDOM() LIMIT ?",
            (size,)
        ))

    payload = {
        "randomSongs": {
            "song": list(map(map_song, songs))
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/stream', methods=["GET", "POST"])
@app.route('/rest/stream.view', methods=["GET", "POST"])
def stream_song():
    r = flask.request.values

    if not bool(flask.g.user_data.get('streamRole')):
        return subsonic_error(50, resp_fmt=r.get('f', 'xml'))

    max_bitrate = int(r.get('maxBitRate', 0))
    req_format = r.get('format') or 'mp3'
    time_offset = float(r.get('timeOffset', 0.0))
    estimate_content_length = r.get('estimateContentLength', 'false').lower() == 'true'

    song_id = sub_to_beets_song(r.get('id'))
    song = flask.g.lib.get_item(song_id)
    song_path = song.get('path', b'').decode('utf-8') if song else ''

    if song_path:
        song_ext = song_path.rsplit('.', 1)[-1].lower() if '.' in song_path else ''

        needs_transcode = False
        if not app.config['never_transcode'] and req_format != 'raw':
            # Transcode if bitrate too high
            if max_bitrate > 0 and song.get('bitrate', 0) > (max_bitrate * 1000):
                needs_transcode = True
            # or if client wants different format
            elif req_format and req_format != song_ext:
                needs_transcode = True

        if not needs_transcode:
            response = stream.direct(song_path)
            est_size = os.path.getsize(song_path) or round(song.get('bitrate', 0) * song.get('length', 0) / 8)
        else:
            target_bitrate = max_bitrate if max_bitrate > 0 else 320

            response = stream.try_transcode(
                song_path,
                start_at=time_offset,
                max_bitrate=target_bitrate,
                req_format=req_format
            )

            remaining_time = max(0, song.get('length', 0) - time_offset)
            est_size = int(((target_bitrate * 1000) / 8) * remaining_time)

        if response is not None:
            if estimate_content_length and est_size:
                response.headers['Content-Length'] = est_size
            return response

    return subsonic_error(70, message="Song not found.", resp_fmt=r.get('f', 'xml'))

@app.route('/rest/download', methods=["GET", "POST"])
@app.route('/rest/download.view', methods=["GET", "POST"])
def download_song():
    r = flask.request.values

    if not bool(flask.g.user_data.get('downloadRole')):
        return subsonic_error(50, resp_fmt=r.get('f', 'xml'))

    song_id = sub_to_beets_song(r.get('id'))
    item = flask.g.lib.get_item(song_id)

    return stream.direct(item.path.decode('utf-8'))


@app.route('/rest/getTopSongs', methods=["GET", "POST"])
@app.route('/rest/getTopSongs.view', methods=["GET", "POST"])
def get_top_songs():

    r = flask.request.values

    req_artist = r.get('id') or r.get('artist') or ''

    if req_artist and req_artist.startswith(ART_ID_PREF):
        artist_name = sub_to_beets_artist(req_artist)
    else:
        artist_name = req_artist

    payload = {'topSongs': {'song': []}}

    # grab the artist's mbid
    with flask.g.lib.transaction() as tx:
        mbid_artist = tx.query("""SELECT mb_artistid FROM items WHERE albumartist LIKE ? LIMIT 1""", (artist_name,))

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
                        'song': list(map(map_song, top_tracks_available))
                    }
                }
                return subsonic_response(payload, r.get('f', 'xml'))

    # Fallback to local play stats
    count = int(r.get('count', 50))
    with connect_dual() as conn:
        rows = conn.execute(
            """
            SELECT i.* 
            FROM beets.items i
            JOIN play_stats ps ON ps.song_id = i.id
            WHERE i.albumartist = ? AND ps.username = ? AND ps.play_count > 0
            ORDER BY ps.play_count DESC 
            LIMIT ?
            """, (artist_name, flask.g.username, count)
        ).fetchall()

    top_tracks_available = [dict(row) for row in rows]

    payload = {
        'topSongs': {
            'song': list(map(map_song, top_tracks_available))
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))




@app.route('/rest/getSimilarSongs', methods=["GET", "POST"])
@app.route('/rest/getSimilarSongs.view', methods=["GET", "POST"])

@app.route('/rest/getSimilarSongs2', methods=["GET", "POST"])
@app.route('/rest/getSimilarSongs2.view', methods=["GET", "POST"])
def get_similar_songs():

    r = flask.request.values

    req_id = r.get('id')
    limit = r.get('count', 50)

    if req_id.startswith(ART_ID_PREF):
        artist_name = sub_to_beets_artist(req_id)
        # grab the artist's mbid
        with flask.g.lib.transaction() as tx:
            mbid_artist = tx.query("SELECT mb_artistid FROM items WHERE albumartist LIKE ? LIMIT 1", (artist_name,))

    elif req_id.startswith(SNG_ID_PREF):
        # TODO - Maybe query the track.getSimilar endpoint on lastfm instead of using the artist?
        beets_song_id = sub_to_beets_song(req_id)
        song_item = flask.g.lib.get_item(beets_song_id)
        if not song_item:
            flask.abort(404)
        artist_name = song_item.get('albumartist', '')
        mbid_artist = [[song_item.get('mb_artistid', '')]]

    elif req_id.startswith(ALB_ID_PREF):
        beets_album_id = sub_to_beets_album(req_id)
        album_object = flask.g.lib.get_album(beets_album_id)
        if not album_object:
            flask.abort(404)
        artist_name = album_object.get('albumartist', '')
        mbid_artist = [[album_object.get('mb_artistid', '')]]

    else:
        return subsonic_error(70, resp_fmt=r.get('f', 'xml'))

    similar_artists = {}

    # If we can ask lastfm
    if app.config['lastfm_api_key']:
        # Query last.fm for similar artists and parse the response
        if mbid_artist:
            lastfm_resp = query_lastfm(q=mbid_artist[0][0], type='artist', method='similar', mbid=True)
        else:
            lastfm_resp = query_lastfm(q=artist_name, type='artist', method='similar', mbid=False)

        if lastfm_resp:

            similar_artists = {
                artist.get('name'): artist.get('mbid', '')
                for artist in lastfm_resp.get('similarartists', {}).get('artist', [])
            }

    # Add the requested artist (will be the only fallback if no lastfm key available)
    similar_artists[artist_name] = mbid_artist[0][0] if mbid_artist else ''

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
        beets_results = list(tx.query(query, params))

    tag = 'similarSongs2' if 'getSimilarSongs2' in flask.request.path else 'similarSongs'
    payload = {
        tag: {
            'song': list(map(map_song, beets_results))
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))
