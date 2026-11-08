from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
import flask

from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.blueprints.shared import route_registrar
from beetsplug.beetstreamnext.core.accounts.users_crud import get_user_roles
from beetsplug.beetstreamnext.core.library.resolve import Resolve
from beetsplug.beetstreamnext.core.media.images import round_image_size, send_album_art
from beetsplug.beetstreamnext.core.media.transcode import FORMAT_MAP, get_normalization_filter, try_transcode
from beetsplug.beetstreamnext.core.runtime.web import send_file
from beetsplug.beetstreamnext.utils.system import resolve_path


def register_playback_routes(bp: flask.Blueprint, login_required: Callable) -> None:
    """
    Admin panel and account page expose the same routes, only the auth decorator differs.
    Open to admins and to users allowed to stream.
    """

    route = route_registrar(bp, login_required)

    def streamer_roles() -> dict:
        roles = get_user_roles(flask.session['username'])
        if not (roles.get('streamRole') or roles.get('adminRole')):
            flask.abort(403)
        return roles

    @route('/stream/<track_id>', ['GET'])
    @with_app_context
    def stream(track_id: str) -> flask.Response:
        """Stream a track in the browser."""

        roles = streamer_roles()
        track = Resolve.song(track_id)
        raw_path = os.fsdecode(track.get('path') or b'') if track else ''
        if not raw_path:
            flask.abort(404)

        path = resolve_path(raw_path, app.config['music_root'])
        extension = Path(path).suffix.lstrip('.').lower()

        max_bitrate = roles.get('maxBitRate') or 0
        normalisation = get_normalization_filter(track)
        too_high = max_bitrate > 0 and (track.get('bitrate') or 0) > max_bitrate * 1000

        if normalisation or too_high or not FORMAT_MAP.get(extension, {}).get('browser_playable'):
            response = try_transcode(
                path,
                max_bitrate=max_bitrate or 320,
                req_format='mp3',
                duration=track.get('length') or 0.0,
                estimate_length=True,
                audio_filters=normalisation,
            )
        else:
            response = send_file(path)

        if response is None:
            flask.abort(404)

        return response

    @route('/art/<int:album_id>', ['GET'])
    @with_app_context
    def art(album_id: int) -> flask.Response:
        streamer_roles()
        size = flask.request.args.get('size', default=0, type=int)

        response = send_album_art(album_id, round_image_size(size))
        if response is None:
            flask.abort(404)

        return response
