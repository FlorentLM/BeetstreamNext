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
from beetsplug.beetstreamnext.utils.system import resolve_path, safe_filename


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

    def visible_playlist(playlist_id: int) -> dict:
        roles = streamer_roles()
        playlist = app.config['playlist_manager'].get(playlist_id)

        if not (playlist and (playlist['public'] or playlist['owner'] == flask.session['username'] or roles.get('adminRole'))):
            flask.abort(404)

        return playlist

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

    @route('/playlists', ['GET'])
    @with_app_context
    def playlists() -> str:
        roles = streamer_roles()
        listing = app.config['playlist_manager'].list_visible(flask.session['username'], is_admin=bool(roles.get('adminRole')))
        return flask.render_template('partials/playlists.html', playlists=listing, bp_name=bp.name)

    @route('/playlists/<int:playlist_id>', ['GET'])
    @with_app_context
    def playlist(playlist_id: int) -> str:
        found = visible_playlist(playlist_id)

        tracks = found['tracks']
        songs = Resolve.songs([t['song_id'] for t in tracks if t['song_id']])

        rows = []
        for position, track in enumerate(tracks, start=1):
            song = songs.get(track['song_id']) if track['song_id'] else None
            rows.append({
                # Tracks missing from library greyed out
                'id': track['song_id'] if song else None,
                'pos': position,
                'title': (song.get('title') if song else None) or track['label'] or track['path'] or '',
                'artist': (song.get('artist') if song else '') or '',
                'album': (song.get('album') if song else '') or '',
                'album_id': song.get('album_id') if song else None,
                'secs': round((song.get('length') if song else track['duration']) or 0),
                '_class': '' if song else 'unmatched',
            })

        return flask.render_template('partials/playlist_tracks.html', playlist=found, rows=rows, bp_name=bp.name)

    @route('/playlists/<int:playlist_id>/export.<fmt>', ['GET'])
    @with_app_context
    def playlist_export(playlist_id: int, fmt: str) -> flask.Response:
        if fmt not in ('m3u', 'csv'):
            flask.abort(404)

        found = visible_playlist(playlist_id)

        manager = app.config['playlist_manager']
        body, mimetype = (manager.m3u_text(found), 'audio/x-mpegurl') if fmt == 'm3u' else (manager.csv_text(found), 'text/csv')

        filename = safe_filename(found['name'], fallback='playlist') + '.' + fmt
        return flask.Response(body, mimetype=mimetype, headers={'Content-Disposition': f'attachment; filename="{filename}"'})
