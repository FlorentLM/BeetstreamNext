from beetsplug.beetstreamnext.utils import *
from beetsplug.beetstreamnext import app, stream
import flask
import re


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

    payload = song_payload(song_id)
    return subsonic_response(payload, r.get('f', 'xml'))

@app.route('/rest/getSongsByGenre', methods=["GET", "POST"])
@app.route('/rest/getSongsByGenre.view', methods=["GET", "POST"])
def songs_by_genre():
    r = flask.request.values

    genre = r.get('genre').replace("'", "\\'")
    count = int(r.get('count') or 10)
    offset = int(r.get('offset') or 0)

    genre_pattern = f"%{genre}%"
    with flask.g.lib.transaction() as tx:
        songs = list(tx.query(
            "SELECT * FROM items WHERE lower(genre) LIKE lower(?) ORDER BY title LIMIT ? OFFSET ?",
            (genre_pattern, count, offset)
        ))

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

    max_bitrate = int(r.get('maxBitRate', 0))
    req_format = r.get('format')
    time_offset = float(r.get('timeOffset', 0.0))
    estimate_content_length = r.get('estimateContentLength', 'false').lower() == 'true'

    song_id = sub_to_beets_song(r.get('id'))
    song = flask.g.lib.get_item(song_id)
    song_path = song.get('path', b'').decode('utf-8') if song else ''

    if song_path:
        if app.config['never_transcode'] or req_format == 'raw' or max_bitrate <= 0 or song.bitrate <= max_bitrate * 1000:
            response = stream.direct(song_path)
            est_size = os.path.getsize(song_path) or round(song.get('bitrate', 0) * song.get('length', 0) / 8)
        else:
            response = stream.try_transcode(song_path, start_at=time_offset, max_bitrate=max_bitrate)
            est_size = int(((max_bitrate * 1000) / 8) * song.get('length', 0))

        if response is not None:
            if estimate_content_length and est_size:
                response.headers['Content-Length'] = est_size
            return response

    subsonic_error(70, message="Song not found.", resp_fmt=r.get('f', 'xml'))

@app.route('/rest/download', methods=["GET", "POST"])
@app.route('/rest/download.view', methods=["GET", "POST"])
def download_song():
    r = flask.request.values

    song_id = sub_to_beets_song(r.get('id'))
    item = flask.g.lib.get_item(song_id)

    return stream.direct(item.path.decode('utf-8'))


@app.route('/rest/getTopSongs', methods=["GET", "POST"])
@app.route('/rest/getTopSongs.view', methods=["GET", "POST"])
def get_top_songs():

    r = flask.request.values

    req_id = r.get('id', '')

    payload = {'topSongs': {'song': []}}

    if req_id.startswith(ART_ID_PREF):
        artist_name = sub_to_beets_artist(req_id)
        # grab the artist's mbid
        with flask.g.lib.transaction() as tx:
            mbid_artist = tx.query(f""" SELECT mb_artistid FROM items WHERE albumartist LIKE '{artist_name}' LIMIT 1 """)

        if app.config['lastfm_api_key']:
            # Query last.fm for top tracks for this artist and parse the response
            if mbid_artist:
                lastfm_resp = query_lastfm(query=mbid_artist[0][0], type='artist', method='TopTracks', mbid=True)
            else:
                lastfm_resp = query_lastfm(query=artist_name, type='artist', method='TopTracks', mbid=False)

            if lastfm_resp:
                beets_results = [flask.g.lib.items(f"""title:{t.get('name', '').replace("'", "")}""")
                                 for t in lastfm_resp.get('toptracks', {}).get('track', [])]
                top_tracks_available = [track[0] for track in beets_results if track]

                payload = {
                    'topSongs': {
                        'song': list(map(map_song, top_tracks_available))
                    }
                }
        else:
            # TODO - Use the local play_count in this case
            pass

    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getStarred', methods=["GET", "POST"])
@app.route('/rest/getStarred.view', methods=["GET", "POST"])

@app.route('/rest/getStarred2', methods=["GET", "POST"])
@app.route('/rest/getStarred2.view', methods=["GET", "POST"])
def get_starred_songs(ver=None):
    # TODO

    r = flask.request.values

    tag = 'starred2' if flask.request.path.rsplit('.', 1)[0].endswith('2') else 'starred'
    payload = {
        tag: {
            'song': []
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
            mbid_artist = tx.query(f""" SELECT mb_artistid FROM items WHERE albumartist LIKE '{artist_name}' LIMIT 1 """)
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
        flask.abort(404)    # just for now

    similar_artists = {}

    # If we can ask lastfm
    if app.config['lastfm_api_key']:
        # Query last.fm for similar artists and parse the response
        if mbid_artist:
            lastfm_resp = query_lastfm(query=mbid_artist[0][0], type='artist', method='similar', mbid=True)
        else:
            lastfm_resp = query_lastfm(query=artist_name, type='artist', method='similar', mbid=False)

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

    # we also let SQL remove duplicate rows using DISTINCT, and apply the limit there directly
    query = "SELECT DISTINCT * FROM items WHERE " + " OR ".join(conditions) + " LIMIT ?"
    params.append(limit)

    # Run the single big SQL query
    with flask.g.lib.transaction() as tx:
        beets_results = list(tx.query(query, params))

    # and finally reply to the client
    tag = 'similarSongs2' if flask.request.path.rsplit('.', 1)[0].endswith('2') else 'similarSongs'
    payload = {
        tag: {
            'song': list(map(map_song, beets_results))
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))
