import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.playlistprovider import Playlist
from beetsplug.beetstreamnext.utils import subsonic_response, subsonic_error, sub_to_beets_song, safe_str
from beetsplug.beetstreamnext.mappings import map_playlist


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getPlaylists/
@app.route('/rest/getPlaylists', methods=['GET', 'POST'])
@app.route('/rest/getPlaylists.view', methods=['GET', 'POST'])
def endpoint_get_playlists():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    # username = r.get('username', default=flask.g.username, type=safe_str)
    playlists = flask.g.playlist_provider.getall()
    # TODO: Properly support support per-user playlists

    payload = {
        'playlists': {
            'playlist': [map_playlist(p) for p in playlists]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getPlaylist/
@app.route('/rest/getPlaylist', methods=['GET', 'POST'])
@app.route('/rest/getPlaylist.view', methods=['GET', 'POST'])
def endpoint_get_playlist():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    playlist_id = r.get('id', default='', type=safe_str)     # Required

    if not playlist_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    playlist = flask.g.playlist_provider.get(playlist_id)

    if playlist is None:
        return subsonic_error(70, resp_fmt=resp_fmt)

    payload = {
        'playlist': map_playlist(playlist, include_songs=True)
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/createPlaylist/
@app.route('/rest/createPlaylist', methods=['GET', 'POST'])
@app.route('/rest/createPlaylist.view', methods=['GET', 'POST'])
def endpoint_create_playlist():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    playlist_id = r.get('playlistId', default='', type=safe_str)     # Required if updating
    name = r.get('name', default='', type=safe_str)[:200]            # Required if creating
    songs_ids = r.getlist('songId', type=safe_str)

    if playlist_id:
        return endpoint_update_playlist()

    if not name:
        return subsonic_error(10, resp_fmt=resp_fmt)

    songs = [flask.g.lib.get_item(sub_to_beets_song(sid)) for sid in songs_ids if sid]
    try:
        playlist = Playlist.from_songs(name, songs)
    except FileExistsError as e:
        return subsonic_error(10, message=str(e), resp_fmt=resp_fmt)

    flask.g.playlist_provider.register(playlist)

    payload = {
        'playlist': map_playlist(playlist)
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/deletePlaylist/
@app.route('/rest/deletePlaylist', methods=['GET', 'POST'])
@app.route('/rest/deletePlaylist.view', methods=['GET', 'POST'])
def endpoint_delete_playlist():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    playlist_id = r.get('id', default='', type=safe_str)     # Required

    if not playlist_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    try:
        flask.g.playlist_provider.delete(playlist_id.lower())
    except FileNotFoundError as e:
        return subsonic_error(70, message=str(e), resp_fmt=resp_fmt)

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/updatePlaylist/
@app.route('/rest/updatePlaylist', methods=['GET', 'POST'])
@app.route('/rest/updatePlaylist.view', methods=['GET', 'POST'])
def endpoint_update_playlist():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    playlist_id = r.get('playlistId', default='', type=safe_str)     # Required
    new_name =  r.get('name', default='', type=safe_str)[:200]
    # new_comment =  r.get('comment', default='', type=safe_str)[:1024]
    # make_public =  r.get('public', default=False, type=api_bool)
    to_add = r.getlist('songIdToAdd', type=safe_str)
    to_remove = r.getlist('songIndexToRemove', type=int)
    # TODO: Playlist comments

    if not playlist_id:
        return subsonic_error(10, 'Playlist ID is required.', resp_fmt=resp_fmt)

    pp = flask.g.playlist_provider

    playlist = pp.get(playlist_id)
    if not playlist:
        return subsonic_error(70, 'Playlist not found.', resp_fmt=resp_fmt)

    try:
        if to_remove:
            playlist.remove_songs(to_remove)

        if to_add:
            beets_items = []

            for s_id in to_add:
                item = flask.g.lib.get_item(sub_to_beets_song(s_id))
                if item:
                    beets_items.append(item)
            playlist.add_songs(beets_items)

        if new_name:
            old_id = playlist.id
            playlist.rename(name=new_name)

            # filename changed so ID changed. Update provider cache.
            pp.deregister(old_id)
            pp.register(playlist)

    except Exception as e:
        app.logger.error(f"Error updating playlist: {e}")
        return subsonic_error(0, message=str(e), resp_fmt=resp_fmt)

    return subsonic_response({}, resp_fmt=resp_fmt)