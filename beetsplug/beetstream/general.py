from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
from beetsplug.beetstream.artists import artist_payload
from beetsplug.beetstream.albums import album_payload
from beetsplug.beetstream.songs import song_payload
import flask


def musicdirectory_payload(subsonic_musicdirectory_id: str, with_artists=True) -> dict:

    # Only one possible root directory in beets (?), so just return its name
    directory_name = app.config['root_directory'].name

    payload = {
        'musicFolders': {
            'musicFolder': [{
                'id': subsonic_musicdirectory_id,
                'name': directory_name
            }]
        }
    }
    return payload


@app.route('/rest/getOpenSubsonicExtensions', methods=["GET", "POST"])
@app.route('/rest/getOpenSubsonicExtensions.view', methods=["GET", "POST"])
def get_open_subsonic_extensions():
    r = flask.request.values

    payload = {
        'openSubsonicExtensions': []
    }
    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getGenres', methods=["GET", "POST"])
@app.route('/rest/getGenres.view', methods=["GET", "POST"])
def get_genres():
    r = flask.request.values

    with flask.g.lib.transaction() as tx:
        mixed_genres = list(tx.query(
            """
            SELECT genre, COUNT(*) AS n_song, '' AS n_album FROM items GROUP BY genre
            UNION ALL
            SELECT genre, '' AS n_song, COUNT(*) AS n_album FROM albums GROUP BY genre
            """))

    g_dict = {}
    for row in mixed_genres:
        genre_field, n_song, n_album = row
        for key in genres_formatter(genre_field):
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


@app.route('/rest/getLicense', methods=["GET", "POST"])
@app.route('/rest/getLicense.view', methods=["GET", "POST"])
def get_license():
    r = flask.request.values

    payload = {
        'license': {
            "valid": True
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))

@app.route('/rest/getMusicFolders', methods=["GET", "POST"])
@app.route('/rest/getMusicFolders.view', methods=["GET", "POST"])
def get_music_folders():
    r = flask.request.values

    payload = musicdirectory_payload(subsonic_musicdirectory_id='m-0', with_artists=False)

    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getMusicDirectory', methods=["GET", "POST"])
@app.route('/rest/getMusicDirectory.view', methods=["GET", "POST"])
def get_music_directory():
    # Works pretty much like a file system
    # Usually Artist first, then Album, then Songs
    r = flask.request.values

    req_id = r.get('id')

    if req_id.startswith(ART_ID_PREF):
        payload = artist_payload(req_id, with_albums=True)  # make sure to include albums
        payload['directory'] = payload.pop('artist')
        payload['directory']['child'] = payload['directory'].pop('album')

    elif req_id.startswith(ALB_ID_PREF):
        payload = album_payload(req_id, with_songs=True)    # make sure to include songs
        payload['directory'] = payload.pop('album')
        payload['directory']['child'] = payload['directory'].pop('song')

    elif req_id.startswith(SNG_ID_PREF):
        payload = song_payload(req_id)
        payload['directory'] = payload.pop('song')

    else:
        payload = musicdirectory_payload('m-0', with_artists=True)

        # TODO - Add missing fields to artist mapper so we can return a directory with artist children
        # payload['directory'] = payload.pop('artist')
        # payload['directory']['child'] = payload['directory'].pop('album')

    return subsonic_response(payload, r.get('f', 'xml'))


