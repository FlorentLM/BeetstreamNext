import ctypes
import hashlib
import mimetypes
import os
import re
import platform
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError
from typing import Optional

from beetsplug.beetstreamnext.core.logging import bsn_logger


##

_VERSION_RE = re.compile(r'\d+(?:\.\d+){1,3}')


def is_installed(package_name: str) -> bool:
    try:
        version(package_name)
        return True
    except PackageNotFoundError:
        return False


def cache_location() -> Path:
    if platform.system() == 'Windows':
        cache_dir = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    elif platform.system() == 'Darwin':
        cache_dir = Path.home() / 'Library' / 'Caches'
    else:
        cache_dir = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))

    final_path = cache_dir / 'beetstreamnext'
    final_path.mkdir(parents=True, exist_ok=True)
    return final_path


def creation_date(filepath: bytes | str | Path) -> float:
    """Get a file's creation date."""

    if isinstance(filepath, bytes):
        filepath = os.fsdecode(filepath)

    stat = Path(filepath).stat()

    if platform.system() == 'Windows':
        return stat.st_ctime

    if platform.system() == 'Darwin':
        return stat.st_birthtime

    # Linux: fall back to mtime
    return getattr(stat, 'st_birthtime', stat.st_mtime)


def get_mimetype(path: bytes | str | Path) -> str:
    """Infer a file's mimetype."""

    if not path:
        return 'application/octet-stream'
    if isinstance(path, bytes):
        path = os.fsdecode(path)

    path = Path(path)

    if '.' not in path.name or path.name.startswith('.'):
        # Assume the passed arg is just an extension
        path = Path('file').with_suffix('.' + path.name.strip('.'))

    mimetype_fallback = {
        '.aac': 'audio/aac',
        '.flac': 'audio/flac',
        '.mp3': 'audio/mpeg',
        '.mp4': 'audio/mp4',
        '.m4a': 'audio/mp4',
        '.ogg': 'audio/ogg',
        '.opus': 'audio/opus'
    }
    ext = path.suffix.lower()
    return mimetypes.guess_type(path)[0] or mimetype_fallback.get(ext, 'application/octet-stream')


def path_hash(path: bytes | str | Path, root_directory: bytes | str | Path) -> str:
    """Short hash of a file's path relative to root_directory."""
    if not path:
        return ''
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    if isinstance(root_directory, bytes):
        root_directory = os.fsdecode(root_directory)
    try:
        rel = Path(path).relative_to(str(root_directory)).as_posix()
    except ValueError:
        rel = Path(path).as_posix()
    return hashlib.sha1(rel.encode('utf-8')).hexdigest()[:16]


def make_hidden(filepath: bytes | str | Path) -> None:
    """Marks a file as hidden on Windows."""
    if isinstance(filepath, bytes):
        filepath = os.fsdecode(filepath)
    filepath = str(filepath)

    if platform.system() == 'Windows':
        try:
            ctypes.windll.kernel32.SetFileAttributesW(filepath, 2)     # 2 is FILE_ATTRIBUTE_HIDDEN
        except Exception as e:
            bsn_logger.warning(f"Could not set file as hidden on Windows: {e}")


_last_logged_ffmpeg: Optional[str] = None
_last_logged_mpv: Optional[str] = None


def find_ffmpeg() -> Optional[str]:
    global _last_logged_ffmpeg
    from beetsplug.beetstreamnext.settings import settings_store
    custom = settings_store.get('ffmpeg_path')
    found = shutil.which(custom) if custom else shutil.which('ffmpeg')
    if found and found != _last_logged_ffmpeg:
        bsn_logger.info(f'ffmpeg found at: {found}')
        _last_logged_ffmpeg = found
    return found


def find_mpv() -> Optional[str]:
    global _last_logged_mpv
    from beetsplug.beetstreamnext.settings import settings_store
    custom = settings_store.get('mpv_path')
    found = shutil.which(custom) if custom else shutil.which('mpv')
    if found and found != _last_logged_mpv:
        bsn_logger.info(f'MPV path: {found}')
        _last_logged_mpv = found
    return found


@lru_cache(maxsize=8)
def binary_version(bin_path: str, version_flag: str) -> Optional[str]:
    """Runs '<bin_path> <version_flag>' and pulls a version number from first line of output."""
    try:
        result = subprocess.run(
            [bin_path, version_flag], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        bsn_logger.warning(f"Could not run '{bin_path} {version_flag}': {e}")
        return None

    first_line = next(iter((result.stdout or result.stderr).splitlines()), '')
    match = _VERSION_RE.search(first_line)
    return match.group(0) if match else None