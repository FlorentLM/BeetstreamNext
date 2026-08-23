import flask

from .. import api_bp

from beetsplug.beetstreamnext.core.playlists import Playlist, PlaylistProvider
from beetsplug.beetstreamnext.settings import settings_store
from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.api.serializers import IDMapper, map_playlist
from beetsplug.beetstreamnext.core.logging import bsn_logger


def _can_read(playlist: Playlist, username: str, is_admin: bool) -> bool:
    """BeetstreamNext playlists ones are per-user. External ones are public."""
    return is_admin or playlist.owner is None or playlist.owner == username


def _can_edit(playlist: Playlist, username: str, is_admin: bool) -> bool:
    """
    Private BSN playlists are only readable and editable by owner (and admin).
    Public BSN playlists are read/write by everyone.
    External playlists (Beets' playlist directory) editing is gated by 'external_playlists_editor's setting.
    Smartplaylist ones are always read-only.
    """

    if playlist.dir_id == PlaylistProvider.SMARTPLAYLIST_DIR_ID:
        return False
    if is_admin:
        return True
    if playlist.owner is not None:
        return playlist.owner == username
    if playlist.dir_id == PlaylistProvider.BSN_DIR_ID:
        return True
    editors = settings_store.get('external_playlists_editors')
    return '*' in editors or username in editors


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getPlaylists/
@api_bp.route('/getPlaylists', methods=['GET', 'POST'])
@api_bp.route('/getPlaylists.view', methods=['GET', 'POST'])
def endpoint_get_playlists() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    username = flask.g.username
    is_admin = bool(flask.g.user_data.get('adminRole'))

    playlists = [
        p for p in flask.g.playlist_provider.getall()
        if _can_read(p, username, is_admin)
    ]

    payload = {
        'playlists': {
            'playlist': [map_playlist(p) for p in playlists]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getPlaylist/
@api_bp.route('/getPlaylist', methods=['GET', 'POST'])
@api_bp.route('/getPlaylist.view', methods=['GET', 'POST'])
def endpoint_get_playlist() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    playlist_id = r.get('id', default='', type=safe_str)     # Required

    if not playlist_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    playlist = flask.g.playlist_provider.get(playlist_id)

    if playlist is None or not _can_read(playlist, flask.g.username, bool(flask.g.user_data.get('adminRole'))):
        return subsonic_error(70, resp_fmt=resp_fmt)

    payload = {
        'playlist': map_playlist(playlist, include_songs=True)
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/createPlaylist/
@api_bp.route('/createPlaylist', methods=['GET', 'POST'])
@api_bp.route('/createPlaylist.view', methods=['GET', 'POST'])
def endpoint_create_playlist() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    playlist_id = r.get('playlistId', default='', type=safe_str)     # Required if updating
    name = r.get('name', default='', type=safe_str)[:200]            # Required if creating
    songs_ids = r.getlist('songId', type=safe_str)

    if playlist_id:
        return endpoint_update_playlist()

    if not name:
        return subsonic_error(10, resp_fmt=resp_fmt)

    songs = [s for sid in songs_ids if sid and (s := IDMapper.resolve_song(sid))]
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
@api_bp.route('/deletePlaylist', methods=['GET', 'POST'])
@api_bp.route('/deletePlaylist.view', methods=['GET', 'POST'])
def endpoint_delete_playlist() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    playlist_id = r.get('id', default='', type=safe_str)     # Required

    if not playlist_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    pp = flask.g.playlist_provider

    playlist = pp.get(playlist_id)
    if not playlist:
        return subsonic_error(70, resp_fmt=resp_fmt)

    if not _can_edit(playlist, flask.g.username, bool(flask.g.user_data.get('adminRole'))):
        return subsonic_error(50, message='Not authorized to delete this playlist.', resp_fmt=resp_fmt)

    try:
        pp.delete(playlist_id)
    except FileNotFoundError as e:
        return subsonic_error(70, message=str(e), resp_fmt=resp_fmt)

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/updatePlaylist/
@api_bp.route('/updatePlaylist', methods=['GET', 'POST'])
@api_bp.route('/updatePlaylist.view', methods=['GET', 'POST'])
def endpoint_update_playlist() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    playlist_id = r.get('playlistId', default='', type=safe_str)     # Required
    new_name =  r.get('name', default='', type=safe_str)[:200]
    new_comment = r.get('comment', default=None, type=safe_str)
    make_public = r.get('public', default=None, type=api_bool)
    to_add = r.getlist('songIdToAdd', type=safe_str)
    to_remove = r.getlist('songIndexToRemove', type=int)

    if not playlist_id:
        return subsonic_error(10, 'Playlist ID is required.', resp_fmt=resp_fmt)

    pp = flask.g.playlist_provider

    playlist = pp.get(playlist_id)
    if not playlist:
        return subsonic_error(70, 'Playlist not found.', resp_fmt=resp_fmt)

    if not _can_edit(playlist, flask.g.username, bool(flask.g.user_data.get('adminRole'))):
        return subsonic_error(50, message='Not authorized to edit this playlist.', resp_fmt=resp_fmt)

    original_id = playlist.id

    try:
        if to_remove:
            playlist.remove_songs(to_remove)

        if to_add:
            beets_items = []

            for s_id in to_add:
                item = IDMapper.resolve_song(s_id)
                if item:
                    beets_items.append(item)
            playlist.add_songs(beets_items)

        if new_comment is not None:
            playlist.set_comment(new_comment[:1024])

        if make_public is not None:
            playlist.set_public(make_public, flask.g.username)

        if new_name:
            playlist.rename(name=new_name)

        # in case ID changed if playlist moved
        if playlist.id != original_id:
            pp.deregister(original_id)
            pp.register(playlist)

    except Exception as e:
        bsn_logger.error(f"Error updating playlist: {e}")
        return subsonic_error(0, message=str(e), resp_fmt=resp_fmt)

    return subsonic_response({}, resp_fmt=resp_fmt)