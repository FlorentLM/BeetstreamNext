import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Tuple, Any
import beets
import confuse
import yaml

from beetsplug.beetstreamnext.core.mappings import Resolve
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import BEETS_IMPORT_LOG_PATH
from beetsplug.beetstreamnext.core.database import write_beets_field
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.settings import settings_store


_lock = threading.Lock()
_process: Optional[subprocess.Popen] = None
_started_at: Optional[float] = None


def _diskwrite_safe() -> bool:
    """
    Returns True if the resolved beets config has no disk-affecting side effects
    (no file modification, moving or copying)
    """
    try:
        return not any(beets.config['import'][key].get(bool) for key in ('move', 'copy', 'write'))
    except confuse.ConfigError:
        return False


def is_importing() -> bool:
    """True while a triggered beets import subprocess is still running."""
    with _lock:
        return _process is not None and _process.poll() is None


def start_import() -> Tuple[bool, str, bool]:
    """
    Trigger an incremental, unattended `beet import` on the library's root directory, as a
    background subprocess.

    Refuses to start if beets' timid mode is on. Setting 'allow_disk_writes' must be on to allow
    any beets configuration that touches the disk (file modification, copy, or write).

    Returns (ok, message, already_running)
    """
    global _process, _started_at

    with _lock:
        if _process is not None and _process.poll() is None:
            return False, 'An import is already running.', True

        if not _diskwrite_safe() and not settings_store.get('allow_disk_writes'):
            return False, ("Refusing to import: the active beets config would write tags or copy/move files. "
                            "Enable 'allow_disk_writes' to allow this."), False

        if beets.config['import']['timid'].get(bool):
            return False, "Can't run incremental import: beets' timid mode is enabled.", False

        root_directory = str(app.config['root_directory'])
        library_path = str(app.config['BEETS_DB_PATH'])

        command = [sys.executable, '-m', 'beets', '-P', 'beetstreamnext']

        config_path = app.config.get('BEETS_CONFIG_PATH')
        if config_path:
            command += ['-c', str(config_path)]

        command += [
            '-l', library_path,
            '-d', root_directory,
            'import', '-q', '-i', root_directory,
        ]

        try:
            log_file = open(BEETS_IMPORT_LOG_PATH, 'wb')
        except OSError as e:
            bsn_logger.error(f'Could not open import log file: {e}')
            return False, 'Failed to start the import (could not open log file).', False

        try:
            proc = subprocess.Popen(
                command, stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
            )
        except Exception as e:
            bsn_logger.error(f'Failed to start beets import: {e}')
            return False, 'Failed to start the import process.', False
        finally:
            log_file.close()   # the child got its own duplicated fd, safe to close ours

        _process = proc
        _started_at = time.time()

        bsn_logger.info(f"Started beets import (pid {proc.pid}): {' '.join(shlex.quote(c) for c in command)}")
        return True, 'Import started.', False


def commit_likes(subsonic_id: str, key: str, value: Any) -> None:
    """
    Apply one user's Likes/Rating value to Beets's db
    (only one user can be applied because Beets is single-user)

    Note: non-song and non-album (artists, playlists, radios, podcasts)
     have no row in to attach a value to, so they are silently skipped.
    """

    entry_type, obj = Resolve.any(subsonic_id)

    if entry_type == 'song':
        entity_type, beets_id = 'item', (obj.id if obj else None)
    elif entry_type == 'album':
        entity_type, beets_id = 'album', (obj.id if obj else None)
    else:
        return

    if beets_id is None:
        return

    try:
        write_beets_field(entity_type, beets_id, key, value, allow_flex=True)
    except Exception as e:
        bsn_logger.warning(f"Failed to mirror '{key}' to beets {entity_type} {beets_id}: {e}")


##
# Beets config load/save

def config_path() -> Path:
    """Path to the beets config that's currently loaded."""
    configured = app.config.get('BEETS_CONFIG_PATH')
    return Path(configured) if configured else Path(beets.config.user_config_path())


def is_config_ro(path: Path) -> bool:
    target = path if path.exists() else path.parent
    return not os.access(target, os.W_OK)


def read_config() -> dict:
    path = config_path()
    try:
        content = path.read_text(encoding='utf-8')
    except OSError:
        content = ''

    return {
        'path': str(path),
        'content': content,
        'read_only': is_config_ro(path),
        'loaded_at': time.time(),
    }


def write_config(content: str) -> Tuple[bool, str]:
    path = config_path()

    if is_config_ro(path):
        return False, 'Config file is read-only.'

    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        return False, f'Invalid YAML: {e}'

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as tf:
                tf.write(content)
            os.replace(tmp_path, path)
        except OSError:
            os.unlink(tmp_path)
            raise
    except OSError as e:
        bsn_logger.error(f"Failed to write beets config '{path}': {e}")
        return False, f'Failed to write file: {e}'

    return True, 'Saved. Restart required for changes to take effect.'
