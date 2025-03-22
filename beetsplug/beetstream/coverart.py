from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
from io import BytesIO
from PIL import Image
import flask
import os
import requests
import subprocess


# TODO - Use python ffmpeg module if available (like in stream.py)

def extract_cover(path) -> BytesIO:
    command = [
        'ffmpeg',
        '-i', path,
        '-vframes', '1',      # extract only one frame
        '-f', 'image2pipe',   # output format is image2pipe
        '-c:v', 'mjpeg',
        '-q:v', '2',          # jpg quality (lower is better)
        'pipe:1'
    ]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    img_bytes, _ = proc.communicate()
    return BytesIO(img_bytes)


def resize_image(data: BytesIO, size: int) -> BytesIO:

    img = Image.open(data)
    img.thumbnail((size, size))

    buf = BytesIO()
    img.save(buf, format='JPEG')
    buf.seek(0)

    return buf


def send_album_art(album_id, size=None):
    """ Generate a response with the album art for given album ID and (optional) size
        (Local file first, then fallback to redirecting to coverarchive.org) """
    album = flask.g.lib.get_album(album_id)
    art_path = album.get('artpath', b'').decode('utf-8')
    if os.path.isfile(art_path):
        if size:
            cover = resize_image(art_path, int(size))
            return flask.send_file(cover, mimetype='image/jpeg')
        return flask.send_file(art_path, mimetype=path_to_content_type(art_path))
    else:
        mbid = album.get('mb_albumid', None)
        if mbid:
            art_url = f'https://coverartarchive.org/release/{mbid}/front'
            if size:
                # If requested size is one of coverarchive's available sizes, query it directly
                if size in (250, 500, 1200):
                    return flask.redirect(f'{art_url}-{size}')
                else:
                    response = requests.get(art_url)
                    cover = resize_image(BytesIO(response.content), int(size))
                    return flask.send_file(cover, mimetype='image/jpeg')
            return flask.redirect(art_url)

    # If nothing found: return empty XML document on error
    # https://opensubsonic.netlify.app/docs/endpoints/getcoverart/
    return subsonic_response({}, 'xml', failed=True)


@app.route('/rest/getCoverArt', methods=["GET", "POST"])
@app.route('/rest/getCoverArt.view', methods=["GET", "POST"])
def get_cover_art():
    r = flask.request.values

    req_id = r.get('id')
    size = r.get('size', None)

    if req_id.startswith(ALB_ID_PREF):
        album_id = int(album_subid_to_beetid(req_id))
        return send_album_art(album_id, size)

    elif req_id.startswith(SNG_ID_PREF):
        item_id = int(song_subid_to_beetid(req_id))
        item = flask.g.lib.get_item(item_id)

        album_id = item.get('album_id', None)
        if album_id:
            return send_album_art(album_id, size)

        cover = extract_cover(item.path)
        if size:
            cover = resize_image(cover, int(size))
        return flask.send_file(cover, mimetype='image/jpeg')

    # TODO - Get artist image if req_id is 'ar-'

    # Fallback: return an empty 'ok' response
    return subsonic_response({}, r.get('f', 'xml'), failed=True)