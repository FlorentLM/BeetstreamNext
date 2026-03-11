import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.playlistprovider import Playlist
from beetsplug.beetstreamnext.utils import map_playlist, subsonic_response, subsonic_error


@app.route('/rest/getPlaylists', methods=['GET', 'POST'])
@app.route('/rest/getPlaylists.view', methods=['GET', 'POST'])
def get_playlists():

    r = flask.request.values

    playlists = flask.g.playlist_provider.getall()

    payload = {
        'playlists': {
            'playlist': [map_playlist(p) for p in playlists]
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getPlaylist', methods=['GET', 'POST'])
@app.route('/rest/getPlaylist.view', methods=['GET', 'POST'])
def get_playlist():
    r = flask.request.values

    playlist_id = r.get('id')
    if not playlist_id:
        return subsonic_error(10, resp_fmt=r.get('f', 'xml'))

    playlist = flask.g.playlist_provider.get(playlist_id)

    if playlist is None:
        return subsonic_error(70, resp_fmt=r.get('f', 'xml'))

    payload = {
        'playlist': map_playlist(playlist)
    }
    return subsonic_response(payload, r.get('f', 'xml'))

@app.route('/rest/createPlaylist', methods=['GET', 'POST'])
@app.route('/rest/createPlaylist.view', methods=['GET', 'POST'])
def create_playlist():

    r = flask.request.values
    resp_fmt = r.get('f', 'xml')

    playlist_id = r.get('playlistId')
    name = r.get('name')
    songs_ids = r.getlist('songId')

    if playlist_id:
        # Update mode: API documentation is unclear so we just return an error; probably better to use updatePlaylist
        return subsonic_error(0, resp_fmt=resp_fmt)

    if not name or not songs_ids:
        return subsonic_error(10, resp_fmt=resp_fmt)

    songs = list(flask.g.lib.items('id:' + ' , id:'.join(songs_ids)))
    try:
        playlist = Playlist.from_songs(name, songs)
    except FileExistsError as e:
        return subsonic_error(10, message=str(e), resp_fmt=resp_fmt)

    flask.g.playlist_provider.register(playlist)
    return subsonic_response({'playlist': map_playlist(playlist)}, resp_fmt)


@app.route('/rest/deletePlaylist', methods=['GET', 'POST'])
@app.route('/rest/deletePlaylist.view', methods=['GET', 'POST'])
def delete_playlist():

    r = flask.request.values
    resp_fmt = r.get('f', 'xml')

    playlist_id = r.get('id')
    if not playlist_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    try:
        flask.g.playlist_provider.delete(playlist_id)
    except FileNotFoundError as e:
        return subsonic_error(70, message=str(e), resp_fmt=resp_fmt)

    return subsonic_response({}, resp_fmt)