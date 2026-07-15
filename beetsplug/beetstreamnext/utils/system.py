import ctypes
import mimetypes
import os
import platform
from pathlib import Path

from beetsplug.beetstreamnext.constants import bsn_logger


##
# Various file access and format detection utilities


def creation_date(filepath) -> float:
    """Get a file's creation date."""

    if platform.system() == 'Windows':
        return os.path.getctime(filepath)

    stat = os.stat(filepath)

    if platform.system() == 'Darwin':
        return stat.st_birthtime

    # Linux: fall back to mtime
    return getattr(stat, 'st_birthtime', stat.st_mtime)


def get_mimetype(path) -> str:
    """Infer a file's mimetype."""
    if not path:
        return 'application/octet-stream'

    path = os.fsdecode(path)
    if '.' not in path or path.startswith('.'):
        # Assume the passed arg is just an extension
        path = f'file.{path}'

    mimetype_fallback = {
        '.aac': 'audio/aac',
        '.flac': 'audio/flac',
        '.mp3': 'audio/mpeg',
        '.mp4': 'audio/mp4',
        '.m4a': 'audio/mp4',
        '.ogg': 'audio/ogg',
        '.opus': 'audio/opus',
        None: 'application/octet-stream'
    }
    return mimetypes.guess_type(path)[0] or mimetype_fallback.get(path.rsplit('.', 1)[-1], 'application/octet-stream')


def make_hidden(filepath: Path) -> None:
    """Marks a file as hidden on Windows."""
    if platform.system() == "Windows":
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(filepath), 2)     # 2 is FILE_ATTRIBUTE_HIDDEN
        except Exception as e:
            bsn_logger.warning(f"Could not set file as hidden on Windows: {e}")
