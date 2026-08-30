import shlex
import subprocess
import sys
import threading
import time
from typing import Optional, Tuple, Any

import beets

from beetsplug.beetstreamnext.api.idmapper import IDMapper
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import CACHE_LOCATION
from beetsplug.beetstreamnext.core.database import write_beets_field
from beetsplug.beetstreamnext.core.logging import bsn_logger

_lock = threading.Lock()
_process: Optional[subprocess.Popen] = None
_started_at: Optional[float] = None

IMPORT_LOG_PATH = CACHE_LOCATION / 'last_import.log'


def is_importing() -> bool:
    """True while a triggered beets import subprocess is still running."""
    with _lock:
        return _process is not None and _process.poll() is None


def start_import() -> Tuple[bool, str, bool]:
    """
    Trigger an incremental, unattended `beet import` on the library's root directory, as a
    background subprocess (in the same Python environment BSN itself runs in) so newly-added
    files that haven't been imported yet get picked up.

    Refuses to start if beets' timid mode is on.

    Returns (ok, message, already_running)
    """
    global _process, _started_at

    with _lock:
        if _process is not None and _process.poll() is None:
            return False, 'An import is already running.', True

        if beets.config['import']['timid'].get(bool):
            return False, "Can't run incremental import because beets' timid mode is enabled.", False

        root_directory = str(app.config['root_directory'])
        library_path = str(app.config['BEETS_DB_PATH'])

        command = [sys.executable, '-m', 'beets']

        config_path = app.config.get('BEETS_CONFIG_PATH')
        if config_path:
            command += ['-c', str(config_path)]

        command += [
            '-l', library_path,
            '-d', root_directory,
            'import', '-q', '-i', root_directory,
        ]

        try:
            log_file = open(IMPORT_LOG_PATH, 'wb')
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

    entry_type, obj = IDMapper.resolve(subsonic_id)

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