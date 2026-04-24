import os
from io import BytesIO

import flask

from . import api_bp

from beetsplug.beetstreamnext.constants import ALB_ID_PREF, SNG_ID_PREF, FFMPEG_PYTHON, FFMPEG_BIN
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.images import (
    round_image_size, send_album_art, thumbnail_path, image_from_song, resize_image, send_artist_image
)
from beetsplug.beetstreamnext.utils import subsonic_error, safe_str, make_hidden, sub_to_beets_album, sub_to_beets_song


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getCoverArt/
@api_bp.route('/getCoverArt', methods=["GET", "POST"])
@api_bp.route('/getCoverArt.view', methods=["GET", "POST"])
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

    # album requests
    if req_id.startswith(ALB_ID_PREF):
        album_id = sub_to_beets_album(req_id)
        response = send_album_art(album_id, size)
        if response is not None:
            return response

    # song requests
    elif req_id.startswith(SNG_ID_PREF):
        item_id = sub_to_beets_song(req_id)
        item = flask.g.lib.get_item(item_id)
        if not item:
            return subsonic_error(70, resp_fmt=resp_fmt)

        album_id = item.get('album_id')
        if album_id:
            response = send_album_art(album_id, size)
            if response is not None:
                return response

        # Fallback: try to extract cover from the song file
        if FFMPEG_PYTHON or FFMPEG_BIN:
            song_path = os.fsdecode(item.path)
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
                    cover_io = resize_image(BytesIO(image_bytes), size)
                    image_bytes = cover_io.getvalue()

                # Save for next time
                try:
                    with open(thumb_path, 'wb') as f:
                        f.write(image_bytes)
                    make_hidden(thumb_path)
                    return flask.send_file(thumb_path, mimetype='image/jpeg')

                except Exception as e:
                    app.logger.warning(f"Failed to cache extracted ffmpeg art: {e}")
                    # can still serve from memory if disk write failed
                    return flask.send_file(BytesIO(image_bytes), mimetype='image/jpeg')

    # TODO: Add playlist images (mosaic of the first 4 albums / songs ?)

    # artist requests
    else:  # some clients ask with artist ID, others ask with artist name, so this catches both
        response = send_artist_image(req_id, size=size)
        if response is not None:
            return response

    return subsonic_error(70, resp_fmt=resp_fmt)