from __future__ import annotations

import hashlib
import re
import subprocess
import os
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from io import BytesIO
from PIL import Image, ImageOps
import flask

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.runtime.web import external_url, read_upload
from beetsplug.beetstreamnext.utils.text import strip_text, validate_mbid
from beetsplug.beetstreamnext.utils.general import subprocess_env
from beetsplug.beetstreamnext.utils.system import get_mimetype, make_hidden, find_binary, resolve_path
from beetsplug.beetstreamnext.constants import (
    MAX_DECODE_PIXELS, FFMPEG_INPUT_ARGS, RAW_ART_MAX_BYTES, AUDIO_EXTENSIONS, MAX_AVATAR_BYTES, MAX_AVATAR_DIM
)
from beetsplug.beetstreamnext.constants import ALLOWED_THUMBNAIL_SIZES, COVER_EXTRACT_MAX_CONCURRENT, IMAGE_EXTENSIONS
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.external.musicbrainz import query_coverartarchive
from beetsplug.beetstreamnext.core.services.external.session import capped_image_fetch
from beetsplug.beetstreamnext.core.library.indexes import ArtistIndex
from beetsplug.beetstreamnext.core.media.artist_images import fetched_image_path, uploaded_image
from beetsplug.beetstreamnext.core.storage.connection import database


_ART_PRIORITY = [
    re.compile(r'^(cover|front|folder|album)$', re.IGNORECASE),     # exact matches
    re.compile(r'.*(cover|front|folder|album).*', re.IGNORECASE),   # partial matches
]

Image.MAX_IMAGE_PIXELS = MAX_DECODE_PIXELS


class ImageTooLarge(ValueError):
    """Raised when an image's declared dimensions exceed the pixel cap."""


def safe_open_image(data: bytes | bytearray | BytesIO) -> Image.Image:
    """Open an image, rejecting oversized inputs before any pixel decode."""
    if isinstance(data, (bytes, bytearray)):
        data = BytesIO(data)
    img = Image.open(data)          # Image.open is lazy, no decode yet
    w, h = img.size
    if w * h > MAX_DECODE_PIXELS:
        raise ImageTooLarge(f'Image dimensions too large: {w}x{h}')
    return img


def sniff_image(data: bytes) -> str | None:
    """Identify jpeg/png/webp/ico/svg from magic bytes. The client mimetype is not trusted."""
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    if data[:4] == b'\x00\x00\x01\x00':
        return 'image/x-icon'
    stripped = data.lstrip(b'\xef\xbb\xbf \t\r\n')
    if stripped.startswith(b'<?xml') or stripped.startswith(b'<svg'):
        return 'image/svg+xml'
    return None


def tokenised_image_url(subsonic_id: str, size: int | None = None) -> str:
    """
    Token-gated URL for an image, to be included inside another endpoint's response.
    """
    if not subsonic_id:
        return ''

    from beetsplug.beetstreamnext.core.media.tokeniser import image_tokeniser

    token = image_tokeniser.register(f'{subsonic_id}|{size or ""}')
    return external_url(flask.url_for('public.tokenised_image', token=token))


def thumbnail_path(original_path: Path | str | bytes, size: int, mtime: float = None) -> Path:
    """Generates unique path for a cached thumbnail."""
    if mtime is None:
        mtime = os.path.getmtime(original_path)

    path_str = os.fsdecode(original_path)
    file_hash = hashlib.md5(f"{path_str}_{size}_{mtime}".encode(), usedforsecurity=False).hexdigest()

    return app.config['THUMBNAIL_CACHE_PATH'] / f'.{file_hash}.jpg'


def round_image_size(requested_size: int | None) -> int | None:
    """Rounds requested image size up to nearest allowed size to limit cache bloat."""
    if not requested_size:
        return None

    for size in ALLOWED_THUMBNAIL_SIZES:
        if requested_size <= size:
            return size

    return ALLOWED_THUMBNAIL_SIZES[-1]   # max size if client asks for something huge


def resize_image(data: bytes | BytesIO, size: int, crop: bool = False) -> BytesIO:
    """
    Applies EXIF rotation, converts to RGB, optionally crops to square, resizes.
    """
    if isinstance(data, bytes):
        data = BytesIO(data)

    img = safe_open_image(data)
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
    try:
        if not album_dir.exists() or not album_dir.is_dir():
            return None

        images = [f for f in album_dir.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS]
    except OSError as e:
        bsn_logger.warning(f"Could not scan folder for art at {album_dir}: {e}")
        return None

    if not images:
        return None

    images = sorted(images) # iterdir order is OS-dependant

    for pattern in _ART_PRIORITY:
        for img in images:
            if pattern.match(img.stem):
                return img

    # or fallback to first image found
    return images[0]


_cover_extractions = threading.BoundedSemaphore(COVER_EXTRACT_MAX_CONCURRENT)


def image_from_song(path: str | Path) -> bytes | None:
    """
    Extracts an embedded picture from an audio file.
    If `path` is a directory, only the first audio file found is tried.
    """
    path = Path(os.fsdecode(path))

    if path.is_dir():
        candidates = sorted(f for f in path.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS)
        if not candidates:
            return None
        path = candidates[0]

    ffmpeg_bin = find_binary('ffmpeg')

    if not _cover_extractions.acquire(timeout=10):
        bsn_logger.warning(f"Too many cover art extractions running, skipping '{path.name}' for now.")
        return None

    try:
        if ffmpeg_bin:
            command = [
                ffmpeg_bin,
                *FFMPEG_INPUT_ARGS,
                '-i', os.fsdecode(path),
                # extract only 1 frame, format image2pipe, jpeg in quality 2 (lower is better)
                '-vframes', '1', '-f', 'image2pipe', '-c:v', 'mjpeg', '-q:v', '2',
                'pipe:1'
            ]
            process = subprocess.Popen(  # noqa: S603 argument list, no shell  # nosec B603 (argument list, no shell)
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=subprocess_env()
            )
            try:
                img_bytes, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate() # flush buffers to avoid zombies
                img_bytes = b''
        else:
            img_bytes = b''
    finally:
        _cover_extractions.release()

    return img_bytes or None


##

def _art_cache_path(album_id, playlist: bool = False) -> Path:
    """Where the cover is kept in the cache directory."""
    return app.config['THUMBNAIL_CACHE_PATH'] / f"{'.playlist-cover' if playlist else '.cover'}-{album_id}.jpg"


def _album_dir(album) -> Path | None:
    raw = os.fsdecode(album.item_dir() or b'')
    return resolve_path(raw, app.config['music_root']) if raw else None


def _cache_album_art(image_bytes: bytes, album, album_dir: Path | None, playlist: bool = False) -> Path | None:
    """
    Keep a fetched cover in the cache and return where.
    If `save_album_art` is enabled, also save it in the album folder as cover.jpg.
    """

    path = _art_cache_path(album.id, playlist)
    try:
        img = safe_open_image(image_bytes).convert('RGB')

        fd, tmp_path = tempfile.mkstemp(dir=path.parent)
        with os.fdopen(fd, 'wb') as f:
            img.save(f, format='JPEG')
        os.replace(tmp_path, path)

        if app.config.get('save_album_art') and not playlist and album_dir and not (album_dir / 'cover.jpg').exists():
            img.save(album_dir / 'cover.jpg', format='JPEG')
            bsn_logger.debug(f"Saved album art for '{album.get('album')}' to {album_dir / 'cover.jpg'}")

        return path

    except Exception as e:
        bsn_logger.warning(f"Could not store album art for album {album.id}: {e}")
        return None


def _album_art_candidates(album) -> Iterator[Path]:
    """
    Beets' artpath > image in the album folder > cover fetched before > query CoverArtArchive
    > embedded in track file > from a playlist foreign url
    """
    # TODO: Reorder this maybe

    art_path = os.fsdecode(album.get('artpath') or b'')
    if art_path and (art_file := resolve_path(art_path, app.config['music_root'])).is_file():
        yield art_file

    album_dir = _album_dir(album)
    if album_dir and (found_art := _image_from_folder(album_dir)):
        yield found_art

    if (cached := _art_cache_path(album.id)).is_file():
        yield cached

    playlist_cover = _art_cache_path(album.id, playlist=True)
    mbid = validate_mbid(album.get('mb_albumid'))

    image_bytes = (
        (query_coverartarchive(mbid) if mbid else b'')
        or (image_from_song(album_dir) if album_dir else b'')
        or (playlist_cover.read_bytes() if playlist_cover.is_file() else b'')
    )

    if image_bytes and (fetched := _cache_album_art(image_bytes, album, album_dir)):
        yield fetched


def _album_art_bytes(album_id, size: int) -> bytes | None:
    """Resized jpg bytes for an album's art at a given size."""

    if album_id is None:
        return None

    album = flask.g.lib.get_album(album_id)
    if not album:
        return None

    for art_file in _album_art_candidates(album):
        resized = _cached_resize(art_file, size)
        if resized:
            return Path(resized).read_bytes() if isinstance(resized, str) else resized.getvalue()

    return None


def fetch_playlist_images(item, url: str) -> None:
    """
    Try to get an image from a #EXTALBUMARTURL line from a foreign m3u
    (if the song's album has no art locally).
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
        return  # beets already has a cover

    album_dir = _album_dir(album)
    if not album_dir:
        return

    if _image_from_folder(album_dir) or _art_cache_path(album_id).is_file() or _art_cache_path(album_id, playlist=True).is_file():
        return  # already has art in folder

    image_bytes = capped_image_fetch(url)
    if image_bytes:
        _cache_album_art(image_bytes, album, album_dir, playlist=True)


def send_album_art(album_id, size=None)  -> flask.Response | None:
    """
    Generates a response with the album art for the given album ID and (optional) size.
    """

    if album_id is None:
        return None

    album = flask.g.lib.get_album(album_id)
    if not album:
        return None

    if size:
        image_bytes = _album_art_bytes(album_id, size)
        return flask.send_file(BytesIO(image_bytes), mimetype='image/jpeg') if image_bytes else None

    # No size requested: serve original file as-is (if not oversized)
    for art_file in _album_art_candidates(album):
        try:
            if art_file.stat().st_size > RAW_ART_MAX_BYTES or art_file.suffix.lower() in ('.tiff', '.tif'):
                resized = _cached_resize(art_file, size=ALLOWED_THUMBNAIL_SIZES[-1])
                return flask.send_file(resized, mimetype='image/jpeg') if resized else None

            return flask.send_file(art_file, mimetype=get_mimetype(art_file))
        except Exception as e:
            bsn_logger.warning(f"Failed to serve art for album {album_id} ({art_file}): {e}")

    return None # TODO - send a placeholder instead of 404ing


def playlist_mosaic(playlist: dict, size: int = 500) -> BytesIO | None:
    """
    2x2 grid mosaic from 4 distinct albums' art in a playlist,
    falls back to a single album art if only one album in the playlist
    """
    from beetsplug.beetstreamnext.core.library.resolve import Resolve

    half = max(size // 2, 1)
    tiles = []
    used_album_ids = []

    for song in Resolve.playlist_songs(playlist['tracks']):
        if not song.album_id or song.album_id in used_album_ids:
            continue

        tile_bytes = _album_art_bytes(song.album_id, half)
        if not tile_bytes:
            continue

        used_album_ids.append(song.album_id)
        tiles.append(tile_bytes)
        if len(tiles) >= 4:
            break

    if not tiles:
        return None
    if len(tiles) == 1:
        return BytesIO(tiles[0])

    cache_key = f"{playlist['id']}_{'-'.join(map(str, used_album_ids))}"
    file_hash = hashlib.md5(f"{cache_key}_{size}".encode(), usedforsecurity=False).hexdigest()
    cache_path = app.config['THUMBNAIL_CACHE_PATH'] / f'.mosaic-{file_hash}.jpg'
    
    if cache_path.is_file():
        return BytesIO(cache_path.read_bytes())

    canvas = Image.new('RGB', (half * 2, half * 2), color=(24, 24, 24))
    positions = [(0, 0), (half, 0), (0, half), (half, half)]

    for i, pos in enumerate(positions):
        try:
            tile_img = safe_open_image(tiles[i % len(tiles)]).convert('RGB')
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


def _send_blob(image: bytes, size: int | None) -> flask.Response | None:
    if size:
        resized = _cached_resize(BytesIO(image), size)
        return flask.send_file(resized, mimetype='image/jpeg') if resized else None
    return flask.send_file(BytesIO(image), mimetype=sniff_image(image) or 'image/jpeg')


def send_artist_image(artist, size=None) -> flask.Response | None:

    from beetsplug.beetstreamnext.core.services.external.artist_image_fetch import artist_image_fetcher
    from beetsplug.beetstreamnext.core.library.ids import IDs
    from beetsplug.beetstreamnext.core.library.resolve import Resolve

    artist = strip_text(artist)

    if IDs.decode_type(artist) == 'artist':
        entry = Resolve.artist(artist)
    else:

        # Just a name: images are fetched per name, any match with that name will do
        index = ArtistIndex.get()
        entry = index.find(artist) or next(iter(index.named(artist)), None)

    if entry is None:
        return None

    # Manual uploads take priority
    manual = uploaded_image(entry.keys)
    if manual:
        return _send_blob(manual, size)

    local_image_path = fetched_image_path(entry.name)

    # Fetch from Deezer if enabled (credit first, then each member of a joint credit)
    if artist_image_fetcher.enabled and not local_image_path.is_file():
        artist_image_fetcher.lookup(entry.keys[0], entry.name, entry.candidates)

    # Serve local if it exists now
    if os.path.isfile(local_image_path):
        if size or os.path.getsize(local_image_path) > RAW_ART_MAX_BYTES:
            resized = _cached_resize(local_image_path, size or ALLOWED_THUMBNAIL_SIZES[-1])
            return flask.send_file(resized, mimetype='image/jpeg') if resized else None

        return flask.send_file(local_image_path, mimetype=get_mimetype(local_image_path))

    return None


def send_stored_art(kind: str, row_id: int, size: int | None = None) -> flask.Response | None:
    """Image stored in the database for a podcast channel or radio station ('podcast' / 'radio')."""

    if kind == 'podcast':
        table = 'podcast_channels'
    elif kind == 'radio':
        table = 'internet_radio_stations'
    else:
        return None

    with database() as db:
        row = db.execute(
            f"""
            SELECT image FROM {table} 
            WHERE id = ?
            """, (row_id,)  # nosec B608  # noqa: S608 vars are constants
        ).fetchone()

    if not row or not row['image']:
        return None

    return _send_blob(row['image'], size)


def read_uploaded_image(field: str = 'avatar') -> bytes | None:
    """Reads, validates and resizes an uploaded square image (avatar, radio icon etc)."""
    data = read_upload(field, MAX_AVATAR_BYTES)
    if data is None:
        return None

    if sniff_image(data) is None:
        raise ValueError('Unsupported or corrupt image. Use JPEG, PNG or WebP.')

    try:
        return resize_image(data, size=MAX_AVATAR_DIM, crop=True).getvalue()
    except (ImageTooLarge, OSError) as e:
        raise ValueError('Unsupported, corrupt, or oversized image.') from e
