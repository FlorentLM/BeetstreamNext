from beetsplug.beetstream.utils import *
import flask
from beetsplug.beetstream import app
from .playlistprovider import PlaylistProvider


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
                'playlist': {
                    'entry': [
                        map_song(
                            flask.g.lib.get_item(int(song['id']))
                        )
                        for song in playlist.songs
                    ]
                }
            }
            return subsonic_response(payload, r.get('f', 'xml'))
    flask.abort(404)