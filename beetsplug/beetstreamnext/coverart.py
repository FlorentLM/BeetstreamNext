import subprocess
import os
from typing import Union
import requests
from io import BytesIO
from PIL import Image
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import (
    FFMPEG_PYTHON, FFMPEG_BIN, ffmpeg,
    get_mimetype, query_deezer,
    ALB_ID_PREF, SNG_ID_PREF, ART_ID_PREF,
    sub_to_beets_artist, sub_to_beets_album, sub_to_beets_song, customstrip)


have_ffmpeg = FFMPEG_PYTHON or FFMPEG_BIN


def extract_cover(path) -> Union[BytesIO, None]:

    if FFMPEG_PYTHON:
        img_bytes, _ = (
            ffmpeg
            .input(path)
            # extract only 1 frame, format image2pipe, jpeg in quality 2 (lower is better)
            .output('pipe:', vframes=1, format='image2pipe', vcodec='mjpeg', **{'q:v': 2})
            .run(capture_stdout=True, capture_stderr=False, quiet=True)
        )

    elif FFMPEG_BIN:
        command = [
            'ffmpeg',
            '-i', path,
            # extract only 1 frame, format image2pipe, jpeg in quality 2 (lower is better)
            '-vframes', '1', '-f', 'image2pipe', '-c:v', 'mjpeg', '-q:v', '2',
            'pipe:1'
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        img_bytes, _ = process.communicate()

    else:
        img_bytes = b''

    return BytesIO(img_bytes) if img_bytes else None


def resize_image(data: BytesIO, size: int) -> BytesIO:
    img = Image.open(data)
    img.thumbnail((size, size))
    buf = BytesIO()
    img.save(buf, format='JPEG')
    buf.seek(0)
    return buf


def send_album_art(album_id, size=None):
    """
    Generates a response with the album art for the given album ID and (optional) size.
    Uses the local file first, then falls back to coverartarchive.org
    """

    album = flask.g.lib.get_album(album_id)
    if not album:
        return None

    # Check Beets db
    art_path = album.get('artpath', b'')
    if art_path and os.path.isfile(art_path):
        if size:
            return flask.send_file(resize_image(BytesIO(open(art_path, 'rb').read()), size), mimetype='image/jpeg')
        return flask.send_file(art_path.decode('utf-8'), mimetype=get_mimetype(art_path.decode('utf-8')))

    # Check disk
    album_dir = album.item_dir()
    if album_dir:
        for cover_name in[b'cover.jpg', b'folder.jpg', b'front.jpg', b'cover.png']:
            possible_path = os.path.join(album_dir, cover_name)
            if os.path.isfile(possible_path):
                if size:
                    return flask.send_file(resize_image(BytesIO(open(possible_path, 'rb').read()), size), mimetype='image/jpeg')
                return flask.send_file(possible_path.decode('utf-8'), mimetype=get_mimetype(possible_path.decode('utf-8')))

    # Proxy from CoverArtArchive
    mbid = album.get('mb_albumid')
    if mbid:
        art_url = f'https://coverartarchive.org/release/{mbid}/front'
        try:
            response = requests.get(art_url, timeout=5)
            if response.ok:
                image_bytes = response.content

                if app.config.get('save_album_art', False) and album_dir:
                    try:
                        save_path = os.path.join(album_dir, b'cover.jpg')
                        if not os.path.exists(save_path):
                            with open(save_path, 'wb') as f:
                                f.write(image_bytes)

                    except (OSError, Exception) as e:
                        app.logger.warning(f"Could not save cover art locally for album {album_id}: {e}")

                if size:
                    cover = resize_image(BytesIO(image_bytes), size)
                    return flask.send_file(cover, mimetype='image/jpeg')

                return flask.send_file(BytesIO(image_bytes), mimetype='image/jpeg')

        except requests.RequestException:
            pass

    return None


def send_artist_image(artist, size=None):

    # TODO - Maybe make a separate plugin to save deezer data permanently to disk / beets db?

    artist = customstrip(artist)
    artist_name = sub_to_beets_artist(artist) if artist.startswith(ART_ID_PREF) else artist

    local_folder = app.config['root_directory'] / artist_name
    if not local_folder.is_dir():
        return None

    local_image_path = local_folder / f'{artist_name}.jpg'

    # Fetch and save if enabled
    if app.config['fetch_artists_images'] and not local_image_path.is_file():
        dz_data = query_deezer(artist=artist_name)

        if dz_data and dz_data.get('type', '') == 'artist':
            img_keys = ['picture_xl', 'picture_big', 'picture_medium', 'picture', 'picture_small']
            k = next(filter(dz_data.get, img_keys), None)
            artist_image_url = dz_data[k]

            if artist_image_url:
                try:
                    response = requests.get(artist_image_url, timeout=5)
                    if response.ok and app.config['save_artists_images']:
                        img = Image.open(BytesIO(response.content))
                        img.save(local_image_path)
                except requests.RequestException:
                    pass

    # Serve local if it exists now
    if os.path.isfile(local_image_path):
        if size:
            cover = resize_image(local_image_path, size)
            return flask.send_file(cover, mimetype='image/jpeg')
        return flask.send_file(local_image_path, mimetype=get_mimetype(local_image_path))

    # Proxy from Deezer (without saving) if local save is off
    if app.config['fetch_artists_images']:
        dz_data = query_deezer(artist=artist_name)

        if dz_data and dz_data.get('type', '') == 'artist':
            available_sizes = [56, 120, 250, 500, 1000]
            target_size = next((s for s in sorted(available_sizes) if size and s >= size), 1000)
            artist_image_url = dz_data.get('picture_small', '').replace('56x56', f'{target_size}x{target_size}')

            if artist_image_url:
                try:
                    response = requests.get(artist_image_url, timeout=5)
                    if response.ok:
                        if size and size != target_size:
                            cover = resize_image(BytesIO(response.content), size)
                            return flask.send_file(cover, mimetype='image/jpeg')

                        return flask.send_file(BytesIO(response.content), mimetype='image/jpeg')

                except requests.RequestException:
                    pass

    return None


@app.route('/rest/getCoverArt', methods=["GET", "POST"])
@app.route('/rest/getCoverArt.view', methods=["GET", "POST"])
def get_cover_art():
    r = flask.request.values

    req_id = r.get('id')
    size = int(r.get('size')) if r.get('size') else None

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
            flask.abort(404)

        album_id = item.get('album_id')
        if album_id:
            response = send_album_art(album_id, size)
            if response is not None:
                return response

        # Fallback: try to extract cover from the song file
        if have_ffmpeg:
            cover_io = extract_cover(item.path)
            if cover_io is not None:
                image_bytes = cover_io.getvalue()
                if size:
                    cover_io = resize_image(BytesIO(image_bytes), size)
                    return flask.send_file(cover_io, mimetype='image/jpeg')
                return flask.send_file(BytesIO(image_bytes), mimetype='image/jpeg')

    # artist requests
    else:
        response = send_artist_image(req_id, size=size)
        if response is not None:
            return response

    # root folder ID or name: serve BeetstreamNext's logo
    if req_id == app.config['root_directory'].name or req_id == 'm-0':
        module_dir = os.path.dirname(os.path.abspath(__file__))
        beetstreamnext_icon = os.path.join(module_dir, '../../beetstreamnext.png')
        return flask.send_file(beetstreamnext_icon, mimetype=get_mimetype(beetstreamnext_icon))

    # TODO - We mighe want to serve artists images when a client requests an artist folder by name (for instance Tempo does this)

    flask.abort(404)
