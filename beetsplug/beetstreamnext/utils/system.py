import ctypes
import hashlib
import mimetypes
import os
import re
import platform
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError
from typing import Dict, Optional

from beetsplug.beetstreamnext.core.logging import bsn_logger


##

_VERSION_RE = re.compile(r'\d+(?:\.\d+){1,3}')


def get_env(var_name: str) -> Optional[str]:
    """Load a env var value, treating unset or empty-string as None."""
    val = os.environ.get(var_name)
    return val if val not in (None, '') else None


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


def config_location() -> Path:
    """Default dir for beetstreamnext.yaml"""

    # if running in a docker container: '/config'
    if Path('/.dockerenv').exists():
        return Path('/config')
    try:
        docker = 'docker' in Path('/proc/1/cgroup').read_text()
    except OSError:
        docker = False

    if docker:
        return Path('/config')

    # Otherwise: OS default
    if platform.system() == 'Windows':
        config_dir = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
    else:
        config_dir = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))

    return config_dir / 'beetstreamnext'


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


# Audio/playlist mimetypes keyed by extension
# (taking precedence over mimetypes.guess() which tends to guess everything is a video...)
AUDIO_MIMETYPES: Dict[str, str] = {
    'mp3': 'audio/mpeg', 'aac': 'audio/aac', 'ogg': 'audio/ogg', 'oga': 'audio/ogg',
    'flac': 'audio/flac', 'wav': 'audio/wav', 'm4a': 'audio/mp4', 'opus': 'audio/opus',
    'mp4': 'audio/mp4', 'm3u8': 'application/x-mpegURL', 'm3u': 'application/x-mpegURL',
}


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

    ext = path.suffix.lower().lstrip('.')
    return AUDIO_MIMETYPES.get(ext) or mimetypes.guess_type(path)[0] or 'application/octet-stream'


def _remap_mount(path_obj: Path, root_directory: bytes | str | Path) -> Path:
    """
    Substitute root_directory for 'library_remote_path' when another container
    and this one mount the music volume at different paths.
    """
    from beetsplug.beetstreamnext.settings import settings_store

    remote = settings_store.get('library_remote_path')
    if not remote:
        return path_obj

    try:
        rel = path_obj.relative_to(remote)
    except ValueError:
        return path_obj

    if isinstance(root_directory, bytes):
        root_directory = os.fsdecode(root_directory)
    return Path(root_directory) / rel


def resolve_path(path: bytes | str | Path, root_directory: bytes | str | Path) -> Path:
    """Absolute Path for a beets-stored path, which may be bytes and/or relative to root_directory."""

    if isinstance(path, bytes):
        path = os.fsdecode(path)
    path_obj = Path(path)

    if isinstance(root_directory, bytes):
        root_directory = os.fsdecode(root_directory)

    if not path_obj.is_absolute():
        path_obj = Path(root_directory) / path_obj

    return _remap_mount(path_obj, root_directory)


def safe_join(base_dir: Path, *parts: str, error: str = 'Path escapes base directory.') -> Path:
    """
    Resolve base_dir joined with parts, raising ValueError if
    the result tries to escape base_dir
    """
    base_dir = base_dir.resolve()
    target = base_dir.joinpath(*parts).resolve()
    if not target.is_relative_to(base_dir):
        raise ValueError(error)
    return target


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


def dir_size(path: Path) -> int:
    """Size (in bytes) of everything under path."""
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())


def purge(folder: Path, max_age: Optional[float] = None, now: Optional[float] = None, suffix: Optional[str] = None) -> int:
    """
    Removes old elements inside a folder: files are deleted, subdirectories
    removed recursively.
    """
    if not folder.exists():
        return 0

    now = time.time() if now is None else now
    n = 0

    for entry in folder.iterdir():
        is_dir = entry.is_dir()

        if suffix:
            if is_dir or entry.suffix != suffix:
                continue
            age = entry.stat().st_mtime
        elif is_dir:
            age = max((f.stat().st_mtime for f in entry.rglob('*') if f.is_file()), default=entry.stat().st_mtime)
        elif entry.is_file():
            age = entry.stat().st_mtime
        else:
            continue

        try:
            if max_age is None or (now - age) > max_age:
                shutil.rmtree(entry) if is_dir else entry.unlink(missing_ok=True)
                n += 1
        except OSError as e:
            bsn_logger.warning(f"Failed to delete '{entry}': {e}")

    return n
