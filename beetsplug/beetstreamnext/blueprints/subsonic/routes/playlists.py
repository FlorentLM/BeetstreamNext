from __future__ import annotations

import flask

from .. import subsonic_bp

from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.core.services.playlists import PlaylistExists, PlaylistNotFound
from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.blueprints.subsonic.responses import subsonic_response, subsonic_error, cap_ids
from beetsplug.beetstreamnext.core.library.resolve import Resolve
from beetsplug.beetstreamnext.core.library.serialise import Serialise

from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger


def _can_edit(playlist: dict, username: str, is_admin: bool) -> bool:
    """
    Private playlists are only editable by their owner (and admins). Public playlists are editable by everyone.
    Mirrored m3u playlists (except smartplaylist's) are editable by admins and 'external_playlists_editors'.
    """

    if playlist['readonly']:
        return False

    if is_admin:
        return True

    if playlist['source']:
        editors = settings_store.get('external_playlists_editors')
        return '*' in editors or username in editors

    return playlist['public'] or playlist['owner'] == username


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getPlaylists/
@subsonic_bp.route('/getPlaylists', methods=['GET', 'POST'])
@subsonic_bp.route('/getPlaylists.view', methods=['GET', 'POST'])
def endpoint_get_playlists() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    manager = flask.g.playlist_manager
    manager.sync_sources()

    is_admin = bool(flask.g.user_data.get('adminRole'))
    playlists = manager.list_visible(flask.g.username, is_admin)

    payload = {
        'playlists': {
            'playlist': [Serialise.playlist(p, read_only=not _can_edit(p, flask.g.username, is_admin))
                         for p in playlists]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getPlaylist/
@subsonic_bp.route('/getPlaylist', methods=['GET', 'POST'])
@subsonic_bp.route('/getPlaylist.view', methods=['GET', 'POST'])
def endpoint_get_playlist() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    playlist_id = r.get('id', default='', type=safe_str)     # Required

    if not playlist_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    playlist = Resolve.playlist(playlist_id)
    if playlist is None:
        return subsonic_error(70, resp_fmt=resp_fmt)

    is_public = bool(playlist['public'])
    is_admin = bool(flask.g.user_data.get('adminRole'))
    is_owner = bool(playlist['owner'] == flask.g.username)

    if not (is_admin or is_public or is_owner):
        return subsonic_error(70, resp_fmt=resp_fmt)

    payload = {
        'playlist': Serialise.playlist(
            playlist, include_songs=True,
            read_only=not _can_edit(playlist, flask.g.username, is_admin)
        )
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/createPlaylist/
@subsonic_bp.route('/createPlaylist', methods=['GET', 'POST'])
@subsonic_bp.route('/createPlaylist.view', methods=['GET', 'POST'])
def endpoint_create_playlist() -> flask.Response:

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    playlist_id = r.get('playlistId', default='', type=safe_str)     # Required if updating
    name = r.get('name', default='', type=safe_str)[:200]            # Required if creating
    songs_ids = cap_ids(r.getlist('songId', type=safe_str))

    if not flask.g.user_data.get('playlistRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    manager = flask.g.playlist_manager

    if playlist_id:

        playlist = Resolve.playlist(playlist_id)
        if not playlist:
            return subsonic_error(70, 'Playlist not found.', resp_fmt=resp_fmt)

        if not _can_edit(playlist, flask.g.username, bool(flask.g.user_data.get('adminRole'))):
            return subsonic_error(50, message='Not authorized to edit this playlist.', resp_fmt=resp_fmt)

        try:
            manager.replace_tracks(playlist['id'], songs_ids)
            if name:
                manager.update(playlist['id'], name=name)
        except (PlaylistExists, PlaylistNotFound) as e:
            return subsonic_error(10, message=str(e), resp_fmt=resp_fmt)

        return subsonic_response({}, resp_fmt=resp_fmt)

    if not name:
        return subsonic_error(10, resp_fmt=resp_fmt)

    try:
        new_id = manager.create(flask.g.username, name, songs_ids)
    except PlaylistExists as e:
        return subsonic_error(10, message=str(e), resp_fmt=resp_fmt)

    new_playlist = manager.get(new_id)
    is_admin = bool(flask.g.user_data.get('adminRole'))

    payload = {
        'playlist': Serialise.playlist(new_playlist, read_only=not _can_edit(new_playlist, flask.g.username, is_admin))
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/deletePlaylist/
@subsonic_bp.route('/deletePlaylist', methods=['GET', 'POST'])
@subsonic_bp.route('/deletePlaylist.view', methods=['GET', 'POST'])
def endpoint_delete_playlist() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    playlist_id = r.get('id', default='', type=safe_str)     # Required

    if not flask.g.user_data.get('playlistRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not playlist_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    playlist = Resolve.playlist(playlist_id)
    if not playlist:
        return subsonic_error(70, resp_fmt=resp_fmt)

    if not _can_edit(playlist, flask.g.username, bool(flask.g.user_data.get('adminRole'))):
        return subsonic_error(50, message='Not authorized to delete this playlist.', resp_fmt=resp_fmt)

    try:
        flask.g.playlist_manager.delete(playlist['id'])
    except PlaylistNotFound as e:
        return subsonic_error(70, message=str(e), resp_fmt=resp_fmt)

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/updatePlaylist/
@subsonic_bp.route('/updatePlaylist', methods=['GET', 'POST'])
@subsonic_bp.route('/updatePlaylist.view', methods=['GET', 'POST'])
def endpoint_update_playlist() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    playlist_id = r.get('playlistId', default='', type=safe_str)     # Required
    new_name =  r.get('name', default='', type=safe_str)[:200]
    new_comment = r.get('comment', default=None, type=safe_str)
    make_public = r.get('public', default=None, type=api_bool)
    to_add = cap_ids(r.getlist('songIdToAdd', type=safe_str))
    to_remove = cap_ids(r.getlist('songIndexToRemove', type=int))

    if not flask.g.user_data.get('playlistRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not playlist_id:
        return subsonic_error(10, 'Playlist ID is required.', resp_fmt=resp_fmt)

    playlist = Resolve.playlist(playlist_id)
    if not playlist:
        return subsonic_error(70, 'Playlist not found.', resp_fmt=resp_fmt)

    if not _can_edit(playlist, flask.g.username, bool(flask.g.user_data.get('adminRole'))):
        return subsonic_error(50, message='Not authorized to edit this playlist.', resp_fmt=resp_fmt)

    manager = flask.g.playlist_manager

    try:
        if to_remove:
            manager.remove_tracks(playlist['id'], positions=to_remove)

        if to_add:
            manager.add_tracks(playlist['id'], to_add)

        manager.update(
            playlist['id'],
            name=new_name or None,
            comment=new_comment[:1024] if new_comment is not None else None,
            public=make_public,
        )

    except Exception as e:
        bsn_logger.error(f"Error updating playlist: {e}")
        return subsonic_error(0, message=str(e), resp_fmt=resp_fmt)

    return subsonic_response({}, resp_fmt=resp_fmt)
