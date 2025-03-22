from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
import os
from typing import Union
import requests
from io import BytesIO
from PIL import Image
import flask


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
    """ Generates a response with the album art for the given album ID and (optional) size
    Uses the local file first, then falls back to coverartarchive.org """

    album = flask.g.lib.get_album(album_id)
    art_path = album.get('artpath', b'').decode('utf-8')
    if os.path.isfile(art_path):
        if size:
            cover = resize_image(art_path, size)
            return flask.send_file(cover, mimetype='image/jpeg')
        return flask.send_file(art_path, mimetype=path_to_mimetype(art_path))

    mbid = album.get('mb_albumid')
    if mbid:
        art_url = f'https://coverartarchive.org/release/{mbid}/front'
        if size:
            # If requested size is one of coverarchive's available sizes, query it directly
            if size in (250, 500, 1200):
                return flask.redirect(f'{art_url}-{size}')
            response = requests.get(art_url)
            cover = resize_image(BytesIO(response.content), size)
            return flask.send_file(cover, mimetype='image/jpeg')
        return flask.redirect(art_url)

    return None


@app.route('/rest/getCoverArt', methods=["GET", "POST"])
@app.route('/rest/getCoverArt.view', methods=["GET", "POST"])
def get_cover_art():
    r = flask.request.values

    req_id = r.get('id')
    size = int(r.get('size')) if r.get('size') else None

    # album requests
    if req_id.startswith(ALB_ID_PREF):
        album_id = int(album_subid_to_beetid(req_id))
        response = send_album_art(album_id, size)
        if response is not None:
            return response

    # song requests
    elif req_id.startswith(SNG_ID_PREF):
        item_id = int(song_subid_to_beetid(req_id))
        item = flask.g.lib.get_item(item_id)
        album_id = item.get('album_id')
        if album_id:
            response = send_album_art(album_id, size)
            if response is not None:
                return response

        # Fallback: try to extract cover from the song file
        if have_ffmpeg:
            cover = extract_cover(item.path)
            if cover is not None:
                if size:
                    cover = resize_image(cover, size)
                return flask.send_file(cover, mimetype='image/jpeg')

    # artist requests
    elif req_id.startswith(ART_ID_PREF):
        # TODO
        pass

    # Fallback: return empty XML document on error
    return subsonic_response({}, 'xml', failed=True)