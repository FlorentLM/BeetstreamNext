import ctypes
import hashlib
import platform
import re
import subprocess
import os
import tempfile
from pathlib import Path
from typing import Union, Optional
from io import BytesIO
from PIL import Image
import flask
from requests import RequestException

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.external import http_session, query_deezer, query_coverartarchive
from beetsplug.beetstreamnext.utils import (
    FFMPEG_PYTHON, FFMPEG_BIN, ffmpeg,
    get_mimetype, ALB_ID_PREF, SNG_ID_PREF, ART_ID_PREF,
    sub_to_beets_artist, sub_to_beets_album, sub_to_beets_song, customstrip, subsonic_error, safe_str
)

have_ffmpeg = FFMPEG_PYTHON or FFMPEG_BIN

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.webp', '.bmp'}
ART_PRIORITY = [
    re.compile(r'^(cover|front|folder|album)$', re.IGNORECASE),     # exact matches
    re.compile(r'.*(cover|front|folder|album).*', re.IGNORECASE),   # partial matches
]
ALLOWED_THUMBNAIL_SIZES = [56, 120, 250, 500, 1000, 1200]


def _thumbnail_path(original_path: Union[Path, str, bytes], size: int, mtime: float = None) -> Path:
    """Generates unique path for a cached thumbnail."""
    if mtime is None:
        mtime = os.path.getmtime(original_path)

    path_str = os.fsdecode(original_path)
    file_hash = hashlib.md5(f"{path_str}_{size}_{mtime}".encode()).hexdigest()

    return app.config['THUMBNAIL_CACHE_PATH'] / f'.{file_hash}.jpg'


def _make_hidden(filepath: Path):
    """Marks a file as hidden on Windows."""
    if platform.system() == "Windows":
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(filepath), 2)     # 2 is FILE_ATTRIBUTE_HIDDEN
        except Exception as e:
            app.logger.warning(f"Could not set file as hidden on Windows: {e}")


##
# Resizing

def _round_size(requested_size: Optional[int]) -> Optional[int]:
    """Rounds requested image size up to nearest allowed size to limit cache bloat."""
    if not requested_size:
        return None

    for size in ALLOWED_THUMBNAIL_SIZES:
        if requested_size <= size:
            return size

    return ALLOWED_THUMBNAIL_SIZES[-1]   # max size if client asks for something huge


def _resize_image(data: BytesIO, size: int) -> BytesIO:
    img = Image.open(data)
    img = img.convert('RGB')
    img.thumbnail((size, size))
    buf = BytesIO()
    img.save(buf, format='JPEG')
    buf.seek(0)
    return buf


def _cached_resize(source_file: Union[Path, str, bytes, BytesIO], size: int) -> Optional[Union[str, BytesIO]]:

    if not source_file:
        return None

    if isinstance(source_file, BytesIO):
        return _resize_image(source_file, size)

    full_path = Path(os.fsdecode(source_file))
    if not full_path.is_file():
        return None

    thumb_path = _thumbnail_path(full_path, size)
    if thumb_path.is_file():
        return str(thumb_path)

    try:  # Generate and save thumbnail
        with open(full_path, 'rb') as f:
            resized_buffer = _resize_image(BytesIO(f.read()), size)

        fd, tmp_path = tempfile.mkstemp(dir=thumb_path.parent)

        with os.fdopen(fd, 'wb') as tf:
            tf.write(resized_buffer.getbuffer())

        os.replace(tmp_path, thumb_path)
        _make_hidden(thumb_path)
        return str(thumb_path)

    except Exception as e:
        app.logger.error(f"Failed to create thumbnail for {full_path}: {e}")
        return None


##
# Image fetching

def _image_from_folder(album_dir: Union[str, Path]) -> Optional[Path]:
    if not album_dir:
        return None

    album_dir = Path(album_dir)
    if not album_dir.exists() or not album_dir.is_dir():
        return None

    images = [f for f in album_dir.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS]
    if not images:
        return None

    images = sorted(images) # iterdir order is OS-dependant

    for pattern in ART_PRIORITY:
        for img in images:
            if pattern.match(img.stem):
                return img

    # or fallback to first image found
    return images[0]


def _image_from_song(path) -> Union[BytesIO, None]:

    if FFMPEG_PYTHON:
        try:
            img_bytes, _ = (
                ffmpeg
                .input(os.fsdecode(path))
                # extract only 1 frame, format image2pipe, jpeg in quality 2 (lower is better)
                .output('pipe:', vframes=1, format='image2pipe', vcodec='mjpeg', **{'q:v': 2})
                .run(capture_stdout=True, capture_stderr=False, quiet=True)
            )
        except ffmpeg.Error:
            img_bytes = b''

    elif FFMPEG_BIN:
        command = [
            'ffmpeg',
            '-i', os.fsdecode(path),
            # extract only 1 frame, format image2pipe, jpeg in quality 2 (lower is better)
            '-vframes', '1', '-f', 'image2pipe', '-c:v', 'mjpeg', '-q:v', '2',
            'pipe:1'
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            img_bytes, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate() # flush buffers to avoid zombies
            img_bytes = b''
    else:
        img_bytes = b''

    return BytesIO(img_bytes) if img_bytes else None


##
# Main logic for album art and for artist images

def send_album_art(album_id, size=None):
    """
    Generates a response with the album art for the given album ID and (optional) size.
    Uses the local file first, then falls back to coverartarchive.org
    """

    album = flask.g.lib.get_album(album_id)
    if not album:
        return None

    # Check Beets db
    art_path = os.fsdecode(album.get('artpath') or b'')
    if art_path and os.path.isfile(art_path):
        try:
            if size:
                return flask.send_file(_cached_resize(art_path, size), mimetype='image/jpeg')

            return flask.send_file(art_path, mimetype=get_mimetype(art_path))
        except Exception as e:
            app.logger.warning(f"Failed to serve image for album {album_id} ({art_path!r}): {e}")

    # Check disk
    album_dir = os.fsdecode(album.item_dir())
    if album_dir:
        found_art = _image_from_folder(album_dir)

        if found_art:
            try:
                if size:
                    resized = _cached_resize(found_art, size)
                    return flask.send_file(resized, mimetype='image/jpeg') if resized else None

                if found_art.suffix.lower() in ('.tiff', '.tif'):
                    resized = _cached_resize(found_art, size=1200)
                    return flask.send_file(resized, mimetype='image/jpeg') if resized else None

                return flask.send_file(found_art, mimetype=get_mimetype(found_art))
            except Exception as e:
                app.logger.warning(f"Failed to serve folder art for album {album_id} ({found_art}): {e}")

    # Proxy from CoverArtArchive
    mbid = album.get('mb_albumid')
    if mbid:
        image_bytes = query_coverartarchive(mbid)
        if image_bytes:
            # Persist to disk if enabled
            if app.config.get('save_album_art') and album_dir:
                save_path = album_dir / 'cover.jpg'
                if not save_path.exists():
                    try:
                        img = Image.open(BytesIO(image_bytes))
                        img.save(save_path, format='JPEG')
                        app.logger.debug(f"Saved album art for '{album.get('album')}' to {save_path}")
                    except Exception as e:
                        app.logger.warning(f"Could not save album art to {save_path}: {e}")

            if size:
                return flask.send_file(_resize_image(BytesIO(image_bytes), size), mimetype='image/jpeg')
            return flask.send_file(BytesIO(image_bytes), mimetype='image/jpeg')

    return None # TODO - send a placeholder instead of 404ing


def send_artist_image(artist, size=None):

    artist = customstrip(artist)
    if artist.startswith(ART_ID_PREF):
        value, is_mbid = sub_to_beets_artist(artist)

        if is_mbid:
            with flask.g.lib.transaction() as tx:
                rows = tx.query(
                    """
                    SELECT albumartist 
                    FROM albums 
                    WHERE mb_albumartistid = ? 
                    LIMIT 1
                    """, (value,)
                )
            artist_name = rows[0][0] if rows else value
        else:
            artist_name = value
    else:
        artist_name = artist

    if not artist_name:
        return None

    local_folder = (app.config['root_directory'] / artist_name).resolve()
    if not local_folder.is_relative_to(app.config['root_directory']):
        return None

    local_image_path = local_folder / f'{artist_name}.jpg'

    if local_folder.is_dir():
        # Try to fetch+save from Deezer if enabled and not already cached
        if app.config['fetch_artists_images'] and not local_image_path.is_file():
            dz_data = query_deezer(artist=artist_name)

            if dz_data and dz_data.get('type', '') == 'artist':
                img_keys = ['picture_xl', 'picture_big', 'picture_medium', 'picture', 'picture_small']
                k = next(filter(dz_data.get, img_keys), None)
                artist_image_url = dz_data[k] if k else None

                if artist_image_url:
                    try:
                        response = http_session().get(artist_image_url, timeout=5)
                        if response.ok and app.config['save_artists_images']:
                            img = Image.open(BytesIO(response.content))
                            img.save(local_image_path)
                    except Exception as e:
                        app.logger.warning(f"Failed to fetch/save artist image for '{artist_name}' from Deezer: {e}")

        # Serve local if it exists now
        if os.path.isfile(local_image_path):
            if size:
                resized = _cached_resize(local_image_path, size)
                return flask.send_file(resized, mimetype='image/jpeg') if resized else None

            return flask.send_file(local_image_path, mimetype=get_mimetype(local_image_path))

    # No local folder/file: proxy from Deezer (without saving) if local save is off
    if app.config['fetch_artists_images']:
        dz_data = query_deezer(artist=artist_name)

        if dz_data and dz_data.get('type', '') == 'artist':
            deezer_avail_sizes = [56, 120, 250, 500, 1000]
            target_size = next((s for s in sorted(deezer_avail_sizes) if size and s >= size), 1000)
            artist_image_url = dz_data.get('picture_small', '').replace('56x56', f'{target_size}x{target_size}')

            if artist_image_url:
                try:
                    response = http_session().get(artist_image_url, timeout=5)
                    if response.ok:
                        if size and size != target_size:
                            cover = _resize_image(BytesIO(response.content), size)
                            return flask.send_file(cover, mimetype='image/jpeg')

                        return flask.send_file(BytesIO(response.content), mimetype='image/jpeg')

                except RequestException:
                    pass
    return None


##
# Endpoints

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getCoverArt/
@app.route('/rest/getCoverArt', methods=["GET", "POST"])
@app.route('/rest/getCoverArt.view', methods=["GET", "POST"])
def endpoint_get_cover_art():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)      # Required
    req_size = r.get('size', default=0, type=int)

    # TODO: Return placeholder images

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    size = _round_size(req_size)

    # root folder ID or name: serve BeetstreamNext's logo
    if req_id == app.config['root_directory'].name or req_id == 'm-0':
        return flask.send_file(app.config['IMAGES_PATH'] / 'beetstreamnext_logo.png', mimetype='image/png')

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
        if have_ffmpeg:
            song_path = os.fsdecode(item.path)
            try:
                song_mtime = os.path.getmtime(song_path)
            except OSError:
                song_mtime = 0.0

            thumb_path = _thumbnail_path(song_path, size or 0, mtime=song_mtime)
            if thumb_path.is_file():
                return flask.send_file(thumb_path, mimetype='image/jpeg')

            cover_io = _image_from_song(song_path)
            if cover_io is not None:
                image_bytes = cover_io.getvalue()

                if size:
                    cover_io = _resize_image(BytesIO(image_bytes), size)
                    image_bytes = cover_io.getvalue()

                # Save for next time
                try:
                    with open(thumb_path, 'wb') as f:
                        f.write(image_bytes)
                    _make_hidden(thumb_path)
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