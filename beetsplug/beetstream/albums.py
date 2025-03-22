from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
import flask
import re
from typing import List
import urllib.parse
from beetsplug.beetstream.artists import artist_payload
from beetsplug.beetstream.songs import song_payload


def album_payload(album_id: str) -> dict:
    album_id = int(album_subid_to_beetid(album_id))
    album = flask.g.lib.get_album(album_id)
    songs = sorted(album.items(), key=lambda s: s.track)

    payload = {
        "album": {
            **map_album(album),
            **{"song": list(map(map_song, songs))}
        }
    }
    return payload


@app.route('/rest/getAlbum', methods=["GET", "POST"])
@app.route('/rest/getAlbum.view', methods=["GET", "POST"])
def get_album():
    r = flask.request.values

    album_id = r.get('id')
    payload = album_payload(album_id)
    return subsonic_response(payload, r.get('f', 'xml'))

@app.route('/rest/getAlbumInfo', methods=["GET", "POST"])
@app.route('/rest/getAlbumInfo.view', methods=["GET", "POST"])
def get_album_info():
    return _album_info()

@app.route('/rest/getAlbumInfo2', methods=["GET", "POST"])
@app.route('/rest/getAlbumInfo2.view', methods=["GET", "POST"])
def get_album_info_2():
    return _album_info(ver=2)

def _album_info(ver=None):
    r = flask.request.values

    req_id = r.get('id')
    album_id = int(album_subid_to_beetid(req_id))
    album = flask.g.lib.get_album(album_id)

    artist_quot = urllib.parse.quote(album.get('albumartist', ''))
    album_quot = urllib.parse.quote(album.get('album', ''))
    lastfm_url = f'https://www.last.fm/music/{artist_quot}/{album_quot}' if artist_quot and album_quot else ''

    tag = f"albumInfo{ver if ver else ''}"
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
def album_list():
    return get_album_list()

@app.route('/rest/getAlbumList2', methods=["GET", "POST"])
@app.route('/rest/getAlbumList2.view', methods=["GET", "POST"])
def album_list_2():
    return get_album_list(ver=2)

def get_album_list(ver=None):

    r = flask.request.values

    sort_by = r.get('type', 'alphabeticalByName')
    size = int(r.get('size', 10))
    offset = int(r.get('offset', 0))
    from_year = int(r.get('fromYear', 0))
    to_year = int(r.get('toYear', 3000))
    genre_filter = r.get('genre')

    # Start building the base query
    query = "SELECT * FROM albums"
    conditions = []
    params = []

    # Apply filtering conditions:
    if sort_by == 'byYear':
        conditions.append("year BETWEEN ? AND ?")
        params.extend([min(from_year, to_year), max(from_year, to_year)])

    if sort_by == 'byGenre' and genre_filter:
        conditions.append("lower(genre) LIKE ?")
        params.append(f"%{genre_filter.lower().strip()}%")

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

    # Add LIMIT and OFFSET for pagination
    query += " LIMIT ? OFFSET ?"
    params.extend([size, offset])

    # Execute the query within a transaction
    with flask.g.lib.transaction() as tx:
        albums = list(tx.query(query, params))

    tag = f"albumList{ver if ver else ''}"
    payload = {
        tag: {
            "album": list(map(map_album, albums))
        }
    }

    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getGenres', methods=["GET", "POST"])
@app.route('/rest/getGenres.view', methods=["GET", "POST"])
def genres():
    r = flask.request.values

    with flask.g.lib.transaction() as tx:
        mixed_genres = list(tx.query(
            """
            SELECT genre, COUNT(*) AS n_song, "" AS n_album FROM items GROUP BY genre
            UNION ALL
            SELECT genre, "" AS n_song, COUNT(*) AS n_album FROM albums GROUP BY genre
            """))

    g_dict = {}
    for row in mixed_genres:
        genre_field, n_song, n_album = row
        for key in genres_splitter(genre_field):
            if key not in g_dict:
                g_dict[key] = [0, 0]
            if n_song:  # Update song count if present
                g_dict[key][0] += int(n_song)
            if n_album: # Update album count if present
                g_dict[key][1] += int(n_album)

    # And convert to list of tuples (only non-empty genres)
    g_list = [(k, *v) for k, v in g_dict.items() if k]
    g_list.sort(key=lambda g: g[1], reverse=True)

    payload = {
        "genres": {
            "genre": [dict(zip(["value", "songCount", "albumCount"], g)) for g in g_list]
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getMusicDirectory', methods=["GET", "POST"])
@app.route('/rest/getMusicDirectory.view', methods=["GET", "POST"])
def musicDirectory():
    # Works pretty much like a file system
    # Usually Artist first, then Album, then Songs
    r = flask.request.values

    req_id = r.get('id')

    if req_id.startswith(ART_ID_PREF):
        payload = artist_payload(req_id)
        payload['directory'] = payload.pop('artist')

    elif req_id.startswith(ALB_ID_PREF):
        payload = album_payload(req_id)
        payload['directory'] = payload.pop('album')
        payload['directory']['child'] = payload['directory'].pop('song')

    elif req_id.startswith(SNG_ID_PREF):
        payload = song_payload(req_id)
        payload['directory'] = payload.pop('song')

    else:
        return flask.abort(404)

    return subsonic_response(payload, r.get('f', 'xml'))