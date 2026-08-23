import hashlib
import re
import subprocess
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union
from io import BytesIO
from PIL import Image, ImageOps
import flask

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.api.idmapper import IDMapper
from beetsplug.beetstreamnext.utils.general import grab_auth_params
from beetsplug.beetstreamnext.utils.text import customstrip, validate_mbid
from beetsplug.beetstreamnext.utils.system import get_mimetype, make_hidden
from beetsplug.beetstreamnext.constants import MAX_DECODE_PIXELS, FFMPEG_PYTHON, FFMPEG_BIN
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.core.external import query_deezer, query_coverartarchive, capped_image_fetch
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.schemas import ALLOWED_THUMBNAIL_SIZES, IMAGE_EXTENSIONS

if TYPE_CHECKING:
    from beetsplug.beetstreamnext.core.playlists import Playlist


_ART_PRIORITY = [
    re.compile(r'^(cover|front|folder|album)$', re.IGNORECASE),     # exact matches
    re.compile(r'.*(cover|front|folder|album).*', re.IGNORECASE),   # partial matches
]

Image.MAX_IMAGE_PIXELS = MAX_DECODE_PIXELS


class ImageTooLarge(ValueError):
    """Raised when an image's declared dimensions exceed the pixel cap."""


def _safe_open_image(data: bytes | bytearray | BytesIO) -> Image.Image:
    """Open an image, rejecting oversized inputs before any pixel decode."""
    if isinstance(data, (bytes, bytearray)):
        data = BytesIO(data)
    img = Image.open(data)          # Image.open is lazy, no decode yet
    w, h = img.size
    if w * h > MAX_DECODE_PIXELS:
        raise ImageTooLarge(f'Image dimensions too large: {w}x{h}')
    return img


def sniff_image(data: bytes) -> str | None:
    """Identify jpeg/png/webp from magic bytes. The client mimetype is not trusted."""
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def image_url(item_id: str, size: Optional[int] = None) -> str:
    if not item_id:
        return ''

    # check if the base URL is already built for the current request, if not, build it
    base_url = getattr(flask.g, '_art_base_url', None)
    if not base_url:
        base_url = flask.url_for('api.endpoint_get_cover_art', _external=True, **grab_auth_params())
        flask.g._art_base_url = base_url

    sep = '&' if '?' in base_url else '?'
    url = f"{base_url}{sep}id={item_id}"
    if size:
        url += f"&size={size}"
    return url


def thumbnail_path(original_path: Path | str | bytes, size: int, mtime: float = None) -> Path:
    """Generates unique path for a cached thumbnail."""
    if mtime is None:
        mtime = os.path.getmtime(original_path)

    path_str = os.fsdecode(original_path)
    file_hash = hashlib.md5(f"{path_str}_{size}_{mtime}".encode()).hexdigest()

    return app.config['THUMBNAIL_CACHE_PATH'] / f'.{file_hash}.jpg'


def round_image_size(requested_size: Optional[int]) -> int | None:
    """Rounds requested image size up to nearest allowed size to limit cache bloat."""
    if not requested_size:
        return None

    for size in ALLOWED_THUMBNAIL_SIZES:
        if requested_size <= size:
            return size

    return ALLOWED_THUMBNAIL_SIZES[-1]   # max size if client asks for something huge


def resize_image(data: Union[bytes, BytesIO], size: int, crop: bool = False) -> BytesIO:
    """
    Applies EXIF rotation, converts to RGB, optionally crops to square, resizes.
    """
    if isinstance(data, bytes):
        data = BytesIO(data)

    img = _safe_open_image(data)
    img = ImageOps.exif_transpose(img)
    img = img.convert('RGB')

    try:
        if crop:
            # Crop to centered square
            img = ImageOps.fit(img, (size, size), method=Image.LANCZOS)
        else:
            # Resize (maintaining aspect ratio)
            img.thumbnail((size, size), resample=Image.LANCZOS)
    except Exception as e:
        bsn_logger.warning(f'Image resizing failed ({e}), storing as-is.')

    out = BytesIO()
    img.save(out, format='JPEG', quality=85, optimize=True)
    out.seek(0)

    return out


def _cached_resize(source_file: Path | str | bytes | BytesIO, size: int) -> str | BytesIO | None:

    if not source_file:
        return None

    if isinstance(source_file, BytesIO):
        return resize_image(source_file, size)

    full_path = Path(os.fsdecode(source_file))
    if not full_path.is_file():
        return None

    thumb_path = thumbnail_path(full_path, size)
    if thumb_path.is_file():
        return str(thumb_path)

    try:  # Generate and save thumbnail
        with open(full_path, 'rb') as f:
            resized_buffer = resize_image(f.read(), size)

        fd, tmp_path = tempfile.mkstemp(dir=thumb_path.parent)

        with os.fdopen(fd, 'wb') as tf:
            tf.write(resized_buffer.getbuffer())

        os.replace(tmp_path, thumb_path)
        make_hidden(thumb_path)
        return str(thumb_path)

    except Exception as e:
        bsn_logger.error(f"Failed to create thumbnail for {full_path}: {e}")
        return None


def _image_from_folder(album_dir: str | Path) -> Path | None:
    if not album_dir:
        return None

    album_dir = Path(album_dir)
    if not album_dir.exists() or not album_dir.is_dir():
        return None

    images = [f for f in album_dir.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS]
    if not images:
        return None

    images = sorted(images) # iterdir order is OS-dependant

    for pattern in _ART_PRIORITY:
        for img in images:
            if pattern.match(img.stem):
                return img

    # or fallback to first image found
    return images[0]


def fetch_playlist_images(item, url: str) -> None:
    """
    Try to get an image from a #EXTALBUMARTURL line from a foreign m3u (if the song's
    album has no art locally). The fetched image is then saved in the album folder as
    'cover.jpg' so the normal folder-scan lookup picks it up.
    """
    if not app.config.get('follow_playlist_embedded_urls') or not url:
        return

    album_id = item.get('album_id')
    if not album_id:
        return

    album = flask.g.lib.get_album(album_id)
    if not album:
        return

    if os.fsdecode(album.get('artpath') or b''):
        return  # beets already knows about a cover

    album_dir_raw = os.fsdecode(album.item_dir() or b'')
    if not album_dir_raw:
        return

    album_dir = Path(album_dir_raw)
    if not album_dir.is_absolute():
        album_dir = app.config['root_directory'] / album_dir

    if _image_from_folder(album_dir):
        return  # already has art on disk

    image_bytes = capped_image_fetch(url)
    if not image_bytes:
        return

    try:
        img = _safe_open_image(image_bytes)
        img.convert('RGB').save(album_dir / 'cover.jpg', format='JPEG')
    except Exception as e:
        bsn_logger.warning(f"Failed to save captured playlist art for album {album_id}: {e}")


def image_from_song(path: str | Path) -> BytesIO | None:

    if FFMPEG_PYTHON:
        import ffmpeg

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

def _persist_album_art(image_bytes: bytes, album, album_dir: Optional[Path]) -> None:
    """Persist CoverArtArchive-fetched bytes as cover.jpg in the album folder, if enabled."""
    if not (app.config.get('save_album_art') and album_dir):
        return
    save_path = album_dir / 'cover.jpg'
    if save_path.exists():
        return
    try:
        img = _safe_open_image(image_bytes)
        img.save(save_path, format='JPEG')
        bsn_logger.debug(f"Saved album art for '{album.get('album')}' to {save_path}")
    except Exception as e:
        bsn_logger.warning(f"Could not save album art to {save_path}: {e}")


def _album_art_bytes(album_id, size: int) -> bytes | None:
    """
    Resised jpg bytes for an album's art at a given size
    (order is local file -> folder scan -> CoverArtArchive).
    """
    if album_id is None:
        return None

    album = flask.g.lib.get_album(album_id)
    if not album:
        return None

    art_path = os.fsdecode(album.get('artpath') or b'')
    if art_path:
        path_obj = Path(art_path)
        if not path_obj.is_absolute():
            art_path = str(app.config['root_directory'] / path_obj)

        if os.path.isfile(art_path):
            resized = _cached_resize(art_path, size)
            if resized:
                return Path(resized).read_bytes() if isinstance(resized, str) else resized.getvalue()

    album_dir_raw = os.fsdecode(album.item_dir() or b'')
    album_dir = None
    if album_dir_raw:
        album_dir = Path(album_dir_raw)
        if not album_dir.is_absolute():
            album_dir = app.config['root_directory'] / album_dir

        found_art = _image_from_folder(album_dir)
        if found_art:
            resized = _cached_resize(found_art, size)
            if resized:
                return Path(resized).read_bytes() if isinstance(resized, str) else resized.getvalue()

    mbid = validate_mbid(album.get('mb_albumid'))
    if mbid:
        image_bytes = query_coverartarchive(mbid)
        if image_bytes:
            _persist_album_art(image_bytes, album, album_dir)
            return resize_image(image_bytes, size).getvalue()

    return None


def send_album_art(album_id, size=None)  -> flask.Response | None:
    """
    Generates a response with the album art for the given album ID and (optional) size.
    Uses the local file first, then falls back to coverartarchive.org
    """

    if album_id is None:
        return None

    album = flask.g.lib.get_album(album_id)
    if not album:
        return None

    if size:
        image_bytes = _album_art_bytes(album_id, size)
        return flask.send_file(BytesIO(image_bytes), mimetype='image/jpeg') if image_bytes else None

    # No size requested: serve original file as-is (in its own format)
    art_path = os.fsdecode(album.get('artpath') or b'')
    if art_path:
        path_obj = Path(art_path)
        if not path_obj.is_absolute():
            art_path = str(app.config['root_directory'] / path_obj)

        if os.path.isfile(art_path):
            try:
                return flask.send_file(art_path, mimetype=get_mimetype(art_path))
            except Exception as e:
                bsn_logger.warning(f"Failed to serve image for album {album_id} ({art_path!r}): {e}")

    # Check disk
    album_dir_raw = os.fsdecode(album.item_dir() or b'')
    album_dir = None
    if album_dir_raw:
        album_dir = Path(album_dir_raw)
        if not album_dir.is_absolute():
            album_dir = app.config['root_directory'] / album_dir

        found_art = _image_from_folder(album_dir)

        if found_art:
            try:
                if found_art.suffix.lower() in ('.tiff', '.tif'):
                    resized = _cached_resize(found_art, size=1200)
                    return flask.send_file(resized, mimetype='image/jpeg') if resized else None

                return flask.send_file(found_art, mimetype=get_mimetype(found_art))
            except Exception as e:
                bsn_logger.warning(f"Failed to serve folder art for album {album_id} ({found_art}): {e}")

    # Proxy from CoverArtArchive
    mbid = validate_mbid(album.get('mb_albumid'))
    if mbid:
        image_bytes = query_coverartarchive(mbid)
        if image_bytes:
            _persist_album_art(image_bytes, album, album_dir)
            return flask.send_file(BytesIO(image_bytes), mimetype='image/jpeg')

    return None # TODO - send a placeholder instead of 404ing


def playlist_mosaic(playlist: 'Playlist', size: int = 500) -> BytesIO | None:
    """
    2x2 grid mosaic from 4 distinct albums' art in a playlist,
    falls back to a single album art if only one album in the playlist
    """
    if not playlist.songs:
        return None

    half = max(size // 2, 1)
    tiles = []
    used_album_ids = []

    for song in playlist.songs:
        sub_album_id = song.get('albumId')
        if not sub_album_id or sub_album_id in used_album_ids:
            continue

        album = IDMapper.resolve_album(sub_album_id)
        if not album:
            continue

        tile_bytes = _album_art_bytes(album.id, half)
        if not tile_bytes:
            continue

        used_album_ids.append(sub_album_id)
        tiles.append(tile_bytes)
        if len(tiles) >= 4:
            break

    if not tiles:
        return None
    if len(tiles) == 1:
        return BytesIO(tiles[0])

    cache_key = f"{playlist.id}_{'-'.join(used_album_ids)}"
    file_hash = hashlib.md5(f"{cache_key}_{size}".encode()).hexdigest()
    cache_path = app.config['THUMBNAIL_CACHE_PATH'] / f'.mosaic-{file_hash}.jpg'
    
    if cache_path.is_file():
        return BytesIO(cache_path.read_bytes())

    canvas = Image.new('RGB', (half * 2, half * 2), color=(24, 24, 24))
    positions = [(0, 0), (half, 0), (0, half), (half, half)]

    for i, pos in enumerate(positions):
        try:
            tile_img = _safe_open_image(tiles[i % len(tiles)]).convert('RGB')
            tile_img = ImageOps.fit(tile_img, (half, half), method=Image.LANCZOS)
            canvas.paste(tile_img, pos)
        except Exception as e:
            bsn_logger.warning(f"Failed to composite mosaic tile: {e}")

    out = BytesIO()
    canvas.save(out, format='JPEG', quality=85, optimize=True)
    out.seek(0)

    try:
        fd, tmp_path = tempfile.mkstemp(dir=cache_path.parent)
        with os.fdopen(fd, 'wb') as tf:
            tf.write(out.getvalue())
        os.replace(tmp_path, cache_path)
        make_hidden(cache_path)
    except Exception as e:
        bsn_logger.warning(f"Failed to cache playlist mosaic: {e}")

    out.seek(0)
    return out


def send_artist_image(artist, size=None) -> flask.Response | None:

    artist = customstrip(artist)
    if IDMapper.get_type(artist) == 'artist':
        value, is_mbid = IDMapper.decode_artist_mbid(artist)

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
                artist_image_url = str(dz_data[k]) if k else None

                if artist_image_url:
                    try:
                        content = capped_image_fetch(artist_image_url, timeout=5)
                        name_is_safe = artist_name not in ('.', '..') and not set(artist_name) & set('/\\')
                        if content and app.config['save_artists_images'] and name_is_safe:
                            img = _safe_open_image(content)
                            img.save(local_image_path)
                    except Exception as e:
                        bsn_logger.warning(f"Failed to fetch/save artist image for '{artist_name}' from Deezer: {e}")

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
                content = capped_image_fetch(artist_image_url, timeout=5)
                if content:
                    try:
                        if size and size != target_size:
                            return flask.send_file(resize_image(content, size), mimetype='image/jpeg')
                        return flask.send_file(BytesIO(content), mimetype='image/jpeg')
                    except (ImageTooLarge, OSError) as e:
                        bsn_logger.warning(f"Bad artist image from Deezer for '{artist_name}': {e}")
            return None

    return None


def send_radio_art(station_id: int) -> flask.Response | None:
    with database() as db:
        row = db.execute(
            """
            SELECT image FROM internet_radio_stations
            WHERE id=?
            """, (station_id,)
        ).fetchone()

    if row and row['image']:
        return flask.send_file(BytesIO(row['image']), mimetype=sniff_image(row['image']))
    return None