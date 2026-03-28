import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.artists import artist_payload
from beetsplug.beetstreamnext.albums import album_payload
from beetsplug.beetstreamnext.songs import song_payload
from beetsplug.beetstreamnext.utils import (
    get_beets_schema, subsonic_response, ART_ID_PREF, ALB_ID_PREF, SNG_ID_PREF, genres_formatter
)


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


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getOpenSubsonicExtensions/
@app.route('/rest/getOpenSubsonicExtensions', methods=["GET", "POST"])
@app.route('/rest/getOpenSubsonicExtensions.view', methods=["GET", "POST"])
def endpoint_get_open_subsonic_extensions():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)

    payload = {
        'openSubsonicExtensions': [
            {
                'name': 'transcodeOffset',
                'versions': [1]
            },
            {
                'name': 'playQueue',
                'versions': [1]
            }
        ]
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getGenres/
@app.route('/rest/getGenres', methods=["GET", "POST"])
@app.route('/rest/getGenres.view', methods=["GET", "POST"])
def endpoint_get_genres():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)

    queries = []

    item_cols = get_beets_schema('items')
    if 'genres' in item_cols:
        queries.append("""SELECT genres AS g, COUNT(*) AS n_s, 0 AS n_a FROM items GROUP BY genres""")
    if 'genre' in item_cols:
        queries.append("""SELECT genre AS g, COUNT(*) AS n_s, 0 AS n_a FROM items GROUP BY genre""")

    alb_cols = get_beets_schema('albums')
    if 'genres' in alb_cols:
        queries.append("""SELECT genres AS g, 0 AS n_s, COUNT(*) AS n_a FROM albums GROUP BY genres""")
    if 'genre' in alb_cols:
        queries.append("""SELECT genre AS g, 0 AS n_s, COUNT(*) AS n_a FROM albums GROUP BY genre""")

    if not queries:
        payload = {
            "genres": {
                "genre": []
            }
        }
        return subsonic_response(payload, resp_fmt=resp_fmt)

    with flask.g.lib.transaction() as tx:
        mixed_genres = list(tx.query(" UNION ALL ".join(queries)))

    g_dict = {}
    for row in mixed_genres:
        genre_field, n_song, n_album = row
        if not genre_field:
            continue

        for key in genres_formatter(genre_field):
            if key not in g_dict:
                g_dict[key] = [0, 0]
            g_dict[key][0] += int(n_song or 0)
            g_dict[key][1] += int(n_album or 0)

    # And convert to list of tuples, remove empty genres, and sort by songCount
    g_list = [(k, *v) for k, v in g_dict.items() if k]
    g_list.sort(key=lambda g: g[1], reverse=True)

    payload = {
        "genres": {
            "genre": [dict(zip(["value", "songCount", "albumCount"], g)) for g in g_list]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getLicense/
@app.route('/rest/getLicense', methods=["GET", "POST"])
@app.route('/rest/getLicense.view', methods=["GET", "POST"])
def endpoint_get_license():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)

    payload = {
        'license': {
            "valid": True
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getMusicFolders/
@app.route('/rest/getMusicFolders', methods=["GET", "POST"])
@app.route('/rest/getMusicFolders.view', methods=["GET", "POST"])
def endpoint_get_music_folders():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)

    payload = musicdirectory_payload(subsonic_musicdirectory_id='m-0', with_artists=False)

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getMusicDirectory/
@app.route('/rest/getMusicDirectory', methods=["GET", "POST"])
@app.route('/rest/getMusicDirectory.view', methods=["GET", "POST"])
def endpoint_get_music_directory():
    # Works pretty much like a file system
    # Usually Artist first, then Album, then Songs
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    req_id = r.get('id', default='', type=str)

    if req_id.startswith(ART_ID_PREF):
        payload = artist_payload(req_id, with_albums=True)
        payload['directory'] = payload.pop('artist')
        payload['directory']['child'] = payload['directory'].pop('album')

    elif req_id.startswith(ALB_ID_PREF):
        payload = album_payload(req_id, with_songs=True)
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

    return subsonic_response(payload, resp_fmt=resp_fmt)


