import os
from io import BytesIO
from pathlib import Path
import flask

from .. import api_bp

from beetsplug.beetstreamnext.constants import FFMPEG_PYTHON
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.utils.system import make_hidden, find_ffmpeg
from beetsplug.beetstreamnext.api.responses import subsonic_error
from beetsplug.beetstreamnext.core.mappings import Resolve

from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.core.images import (
    round_image_size, send_album_art, thumbnail_path, playlist_mosaic, image_from_song,
    resize_image, send_artist_image, send_radio_art, send_podcast_art
)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getCoverArt/
@api_bp.route('/getCoverArt', methods=['GET', 'POST'])
@api_bp.route('/getCoverArt.view', methods=['GET', 'POST'])
def endpoint_get_cover_art() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)      # Required
    req_size = r.get('size', default=0, type=int)

    # TODO: Return placeholder images

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    size = round_image_size(req_size)

    # root folder ID or name: serve BeetstreamNext's logo
    if req_id == app.config['root_directory'].name or req_id == 'm-0':
        return flask.send_file(app.config['IMAGES_PATH'] / 'logo.png', mimetype='image/png')

    entry_type, entry = Resolve.any(req_id)

    # album requests
    if entry_type == 'album':
        response = send_album_art(entry.id, size) if entry else None
        if response is not None:
            return response

    # song requests
    elif entry_type == 'song':
        item = entry
        if not item:
            return subsonic_error(70, resp_fmt=resp_fmt)

        album_id = item.get('album_id')
        if album_id:
            response = send_album_art(album_id, size)
            if response is not None:
                return response

        # Fallback: try to extract cover from the song file
        if FFMPEG_PYTHON or find_ffmpeg():
            song_path = os.fsdecode(item.path)
            path_obj = Path(song_path)
            if not path_obj.is_absolute():
                song_path = str(app.config['root_directory'] / path_obj)
            try:
                song_mtime = os.path.getmtime(song_path)
            except OSError:
                song_mtime = 0.0

            thumb_path = thumbnail_path(song_path, size or 0, mtime=song_mtime)
            if thumb_path.is_file():
                return flask.send_file(thumb_path, mimetype='image/jpeg')

            cover_io = image_from_song(song_path)
            if cover_io is not None:
                image_bytes = cover_io.getvalue()

                if size:
                    cover_io = resize_image(image_bytes, size)
                    image_bytes = cover_io.getvalue()

                # Save for next time
                try:
                    with open(thumb_path, 'wb') as f:
                        f.write(image_bytes)
                    make_hidden(thumb_path)
                    return flask.send_file(thumb_path, mimetype='image/jpeg')

                except Exception as e:
                    bsn_logger.warning(f"Failed to cache extracted ffmpeg art: {e}")
                    # can still serve from memory if disk write failed
                    return flask.send_file(BytesIO(image_bytes), mimetype='image/jpeg')

    elif entry_type == 'radio':
        if entry:
            response = send_radio_art(entry['id'])
            if response is not None:
                return response

    elif entry_type == 'podcastChannel':
        if entry:
            response = send_podcast_art(entry['id'], size)
            if response is not None:
                return response

    elif entry_type == 'episode':
        if entry:
            response = send_podcast_art(entry['channel_id'], size)
            if response is not None:
                return response

    elif entry_type == 'playlist':
        playlist = flask.g.playlist_provider.get(req_id)
        if playlist:
            mosaic = playlist_mosaic(playlist, size or 500)
            if mosaic is not None:
                return flask.send_file(mosaic, mimetype='image/jpeg')

    # artist requests
    else:  # some clients ask with artist ID, others ask with artist name, so this catches both
        response = send_artist_image(req_id, size=size)
        if response is not None:
            return response

    return subsonic_error(70, resp_fmt=resp_fmt)