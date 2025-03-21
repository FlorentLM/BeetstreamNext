from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
import flask
from .playlistprovider import PlaylistProvider

_playlist_provider = PlaylistProvider('')

# TODO link with https://beets.readthedocs.io/en/stable/plugins/playlist.html
@app.route('/rest/getPlaylists', methods=['GET', 'POST'])
@app.route('/rest/getPlaylists.view', methods=['GET', 'POST'])
def playlists():
    r = flask.request.values

    playlists = playlist_provider().playlists()

    payload = {
        'playlists': {
            'playlist': [map_playlist(p) for p in playlists]
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))

@app.route('/rest/getPlaylist', methods=['GET', 'POST'])
@app.route('/rest/getPlaylist.view', methods=['GET', 'POST'])
def playlist():
    r = flask.request.values

    playlist_id = r.get('id')
    playlist = playlist_provider().playlist(playlist_id)
    items = playlist.items()

    payload = {
        'playlist': {
            'entry': [
                map_song(
                    flask.g.lib.get_item(int(item.attrs['id']))
                )
                for item in items
            ]
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))


def playlist_provider():
    if 'playlist_dir' in app.config:
        _playlist_provider.dir = app.config['playlist_dir']
    if not _playlist_provider.dir:
        app.logger.warning('No playlist_dir configured')
    return _playlist_provider
