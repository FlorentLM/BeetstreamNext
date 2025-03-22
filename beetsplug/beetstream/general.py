from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
from beetsplug.beetstream.artists import artist_payload
from beetsplug.beetstream.albums import album_payload
from beetsplug.beetstream.songs import song_payload
import flask

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