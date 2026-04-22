from typing import Dict

import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.artists import artist_payload
from beetsplug.beetstreamnext.albums import album_payload
from beetsplug.beetstreamnext.songs import song_payload
from beetsplug.beetstreamnext.utils import (
    get_beets_schema, subsonic_response, ART_ID_PREF, ALB_ID_PREF, SNG_ID_PREF, genres_formatter, subsonic_error,
    beets_to_sub_artist, safe_str
)


def musicdirectory_payload(subsonic_musicdirectory_id: str) -> Dict:

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
def endpoint_get_open_subsonic_extensions() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

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
def endpoint_get_genres() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

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
def endpoint_get_license() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    payload = {
        'license': {
            "valid": True
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getMusicFolders/
@app.route('/rest/getMusicFolders', methods=["GET", "POST"])
@app.route('/rest/getMusicFolders.view', methods=["GET", "POST"])
def endpoint_get_music_folders() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    payload = musicdirectory_payload(subsonic_musicdirectory_id='m-0', with_artists=False)

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getMusicDirectory/
@app.route('/rest/getMusicDirectory', methods=["GET", "POST"])
@app.route('/rest/getMusicDirectory.view', methods=["GET", "POST"])
def endpoint_get_music_directory() -> flask.Response:
    # Works pretty much like a file system
    # Usually Artist first, then Album, then Songs
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)      # Required

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    if req_id.startswith(ART_ID_PREF):
        payload = artist_payload(req_id, with_albums=True)
        payload['directory'] = payload.pop('artist')
        payload['directory']['child'] = payload['directory'].pop('album')

    elif req_id.startswith(ALB_ID_PREF):
        payload = album_payload(req_id, include_songs=True)
        payload['directory'] = payload.pop('album')
        payload['directory']['child'] = payload['directory'].pop('song')

    elif req_id.startswith(SNG_ID_PREF):
        payload = song_payload(req_id)
        payload['directory'] = payload.pop('song')

    else:
        with flask.g.lib.transaction() as tx:
            rows = tx.query(
                """
                SELECT albumartist, mb_albumartistid
                FROM albums 
                WHERE albumartist IS NOT NULL 
                GROUP BY albumartist
                """
            )

        payload = musicdirectory_payload('m-0', with_artists=True)
        payload['directory'] = payload.pop('musicFolders')['musicFolder'][0]

        children = []
        for row in rows:
            artist_name, artist_mbid = row
            if artist_mbid:
                artist_id = beets_to_sub_artist(artist_mbid)
            else:
                artist_id = beets_to_sub_artist(artist_name, is_mbid=False)
            children.append({
                'id': artist_id,
                'title': artist_name,
                'isDir': True,
                'artist': artist_name,
                'coverArt': artist_id,
            })
        payload['directory']['child'] = children

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/ping/
@app.route('/rest/ping', methods=["GET", "POST"])
@app.route('/rest/ping.view', methods=["GET", "POST"])
def endpoint_ping() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/startScan/
@app.route('/rest/startScan', methods=["GET", "POST"])
@app.route('/rest/startScan.view', methods=["GET", "POST"])
def endpoint_start_scan() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    # TODO: maybe trigger a refresh of BeetstreamNext's data (album covers, etc)?

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getScanStatus/
@app.route('/rest/getScanStatus', methods=["GET", "POST"])
@app.route('/rest/getScanStatus.view', methods=["GET", "POST"])
def endpoint_get_scan_status() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    with flask.g.lib.transaction() as tx:
        items_count = tx.query("SELECT COUNT(*) FROM items")[0][0]

    payload = {
        'scanStatus': {
            "scanning": False,
            "count": items_count
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/tokenInfo/
@app.route('/rest/tokenInfo', methods=["GET", "POST"])
@app.route('/rest/tokenInfo.view', methods=["GET", "POST"])
def endpoint_token_info() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    api_key = r.get('apiKey', default='', type=str)

    if not api_key:
        return subsonic_error(10, resp_fmt=resp_fmt)

    from beetsplug.beetstreamnext.users import load_username
    import hashlib

    api_key_hash = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
    username = load_username(api_key_hash)

    if not username:
        return subsonic_error(40, resp_fmt=resp_fmt)

    payload = {
        'tokenInfo': {
            'username': username
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getPodcasts/
@app.route('/rest/getPodcasts', methods=["GET", "POST"])
@app.route('/rest/getPodcasts.view', methods=["GET", "POST"])
def endpoint_get_podcasts() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    return subsonic_error(0, message='Podcast feature is not supported.', resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getInternetRadioStations/
@app.route('/rest/getInternetRadioStations', methods=["GET", "POST"])
@app.route('/rest/getInternetRadioStations.view', methods=["GET", "POST"])
def endpoint_get_radios() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    return subsonic_error(0, message='Radio feature is not supported.', resp_fmt=resp_fmt)