from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
from io import BytesIO
from PIL import Image
import flask
import os
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


@app.route('/rest/getCoverArt', methods=["GET", "POST"])
@app.route('/rest/getCoverArt.view', methods=["GET", "POST"])
def get_cover_art():
    r = flask.request.values

    req_id = r.get('id')
    size = r.get('size', None)

    if req_id.startswith(ALB_ID_PREF):

        album_id = int(album_subid_to_beetid(req_id))
        album = flask.g.lib.get_album(album_id)

        art_path = album.get('artpath', b'').decode('utf-8')
        print(art_path)

        if os.path.isfile(art_path):
            if size:
                cover = resize_image(art_path, int(size))
                return flask.send_file(cover, mimetype='image/jpg')
            return flask.send_file(art_path, mimetype=path_to_content_type(art_path))

        # TODO - Query from coverartarchive.org if no local file found

    elif req_id.startswith(SNG_ID_PREF):
        item_id = int(song_subid_to_beetid(req_id))
        item = flask.g.lib.get_item(item_id)

        # TODO - try to get the album's cover first, then extract only if needed
        cover = extract_cover(item.path)
        if size:
            cover = resize_image(cover, int(size))

        return flask.send_file(cover, mimetype='image/jpg')


    # TODO - Get artist image if req_id is 'ar-'

    # Fallback: return an empty 'ok' response
    return subsonic_response({}, r.get('f', 'xml'))