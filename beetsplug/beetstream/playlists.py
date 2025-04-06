from beetsplug.beetstream.utils import *
import flask
from beetsplug.beetstream import app
from .playlistprovider import PlaylistProvider, Playlist


@app.route('/rest/getPlaylists', methods=['GET', 'POST'])
@app.route('/rest/getPlaylists.view', methods=['GET', 'POST'])
def get_playlists():

    r = flask.request.values

    # Lazily initialize the playlist provider the first time it's needed
    if not hasattr(flask.g, 'playlist_provider'):
        flask.g.playlist_provider = PlaylistProvider()

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
    if playlist_id:

        # Lazily initialize the playlist provider the first time it's needed
        if not hasattr(flask.g, 'playlist_provider'):
            flask.g.playlist_provider = PlaylistProvider()

        playlist = flask.g.playlist_provider.get(playlist_id)

        if playlist is not None:
            payload = {
                'playlist': map_playlist(playlist)
            }
            return subsonic_response(payload, r.get('f', 'xml'))

    return subsonic_error(70, r.get('f', 'xml'))


@app.route('/rest/createPlaylist', methods=['GET', 'POST'])
@app.route('/rest/createPlaylist.view', methods=['GET', 'POST'])
def create_playlist():

    r = flask.request.values

    playlist_id = r.get('playlistId')
    name = r.get('name')
    songs_ids = r.getlist('songId')

    # Lazily initialize the playlist provider the first time it's needed
    if not hasattr(flask.g, 'playlist_provider'):
        flask.g.playlist_provider = PlaylistProvider()

    if playlist_id:
        # Update mode: API documentation is unclear so we just return an error; probably better to use updatePlaylist
        return subsonic_error(0, r.get('f', 'xml'))
    elif name and songs_ids:
        # Create mode
        songs = list(flask.g.lib.items('id:' + ' , id:'.join(songs_ids)))
        try:
            playlist = Playlist.from_songs(name, songs)
        except FileExistsError as e:
            return subsonic_error(10, message=str(e), resp_fmt=r.get('f', 'xml'))

        flask.g.playlist_provider.register(playlist)

        payload = {
            'playlist': map_playlist(playlist)
        }
        return subsonic_response(payload, r.get('f', 'xml'))

    return subsonic_error(10, r.get('f', 'xml'))