from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Tuple, Any, List
import beets
import confuse
import yaml

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import BEETS_IMPORT_LOG_PATH, ALPHANUM_CHARS
from beetsplug.beetstreamnext.core.connection import dual_database
from beetsplug.beetstreamnext.utils.db import get_beets_schema
from beetsplug.beetstreamnext.core.events import admin_events
from beetsplug.beetstreamnext.core.import_paths import mark_pinned_triggered, validate_pinned_path
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.settings import settings_store
from beetsplug.beetstreamnext.utils.ansi import ansi_to_html
from beetsplug.beetstreamnext.utils.system import is_writable, read_log

IS_WINDOWS = sys.platform == 'win32'

if IS_WINDOWS:
    from winpty import PtyProcess
else:
    import pty

_lock = threading.RLock()

_proc: Optional[Any] = None         # subprocess.Popen or winpty.PtyProcess
_master_fd: Optional[int] = None    # POSIX only, Windows pty process reads/writes itself
_state: str = 'idle'                # idle, running, needs_input, completed, failed
_current_path: Optional[str] = None
_exit_code: Optional[int] = None
_queue: List[dict] = []             # pinned paths waiting to be scanned

_NEEDS_INPUT_SNIPPETS = (
    '[A]pply', 'kip new', 'ter search', 'it, edit ',
    '# selection (default', 'ore candidates',
    'nter search, enter', 'This album is already in the library!',
)


def is_import_safe() -> bool:
    """
    Returns True if the resolved beets config has no disk-affecting side effects
    (no file modification, moving or copying)
    """
    try:
        return not any(beets.config['import'][key].get(bool) for key in ('move', 'copy', 'write'))
    except confuse.ConfigError:
        return False


def import_status() -> dict:
    """Snapshot of the current import state."""
    with _lock:
        return {'state': _state, 'path': _current_path, 'exit_code': _exit_code}


def is_import_running() -> bool:
    """Import session is running or waiting on input."""
    return import_status()['state'] in ('running', 'needs_input')


def htmlify_log() -> List[str]:
    """Import log as HTML lines (escaped, ANSI converted)."""
    return [ansi_to_html(line) for line in read_log(BEETS_IMPORT_LOG_PATH, drop_python=True)]


def _push_import_status() -> None:
    """Publish import status and recent log to connected admin sessions."""
    admin_events.publish('beets-import', {**import_status(), 'lines': htmlify_log()})


def start_import(path: str, quiet: bool = False, incremental: bool = False, reset_log: bool = True) -> Tuple[bool, str]:
    """
    Start a `beet import` against `path`.
    """
    global _proc, _master_fd, _state, _current_path, _exit_code

    with _lock:
        if is_import_running():
            return False, 'An import is already running.'

        candidate = Path(path).expanduser() if path else None
        if not candidate or not candidate.is_dir():
            return False, f"'{path}' is not a directory."

        if not candidate.is_absolute():
            return False, 'Use an absolute path.'

        # Pins are validated when added, but the library root or a symlink may have changed since
        if quiet:
            error = validate_pinned_path(candidate)
            if error:
                return False, error

        if not is_import_safe() and not settings_store.get('allow_disk_writes'):
            return False, ("Refusing to import: beets config is set to allow writing tags or copying/moving files. "
                            "Enable 'allow_disk_writes' in BeetstreamNext to allow this.")

        library_path = str(app.config['BEETS_DB_PATH'])

        command = [sys.executable, '-m', 'beets', '-P', 'beetstreamnext']

        config_path = app.config.get('BEETS_CONFIG_PATH')
        if config_path:
            command += ['-c', str(config_path)]

        command += ['-l', library_path, 'import']
        if quiet:
            command += ['-q', '--incremental' if incremental else '--noincremental']
        command.append(str(candidate))

        env = dict(os.environ)
        env.pop('NO_COLOR', None)
        env.setdefault('TERM', 'xterm-256color')

        if reset_log:
            try:
                open(BEETS_IMPORT_LOG_PATH, 'wb').close()
            except OSError as e:
                bsn_logger.warning(f'Could not reset import log file: {e}')
        else:
            try:
                with open(BEETS_IMPORT_LOG_PATH, 'a', encoding='utf-8') as f:
                    f.write(f"\n[beetstreamnext] Next pinned path: {candidate}\n")
            except OSError:
                pass

        if IS_WINDOWS:
            try:
                proc = PtyProcess.spawn(command, env=env)
            except Exception as e:
                bsn_logger.error(f'Failed to start beets import: {e}')
                return False, 'Failed to start the import process.'

            _proc = proc
            _master_fd = None
            pump_thread = threading.Thread(target=_pump_output_windows, args=(proc,), daemon=True)
            pid_for_log = proc.pid

        else:
            master_fd, slave_fd = pty.openpty()
            try:
                proc = subprocess.Popen(
                    command, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                    close_fds=True, start_new_session=True, env=env,
                )

            except Exception as e:
                os.close(master_fd)
                os.close(slave_fd)
                bsn_logger.error(f'Failed to start beets import: {e}')
                return False, 'Failed to start the import process.'

            finally:
                os.close(slave_fd)

            _proc = proc
            _master_fd = master_fd
            pump_thread = threading.Thread(target=_pump_output_posix, args=(proc, master_fd), daemon=True)
            pid_for_log = proc.pid

        _current_path = str(candidate)
        _state = 'running'
        _exit_code = None

        pump_thread.start()

        bsn_logger.info(f"Started beets import (pid {pid_for_log}) on '{candidate}': "
                         f"{' '.join(shlex.quote(c) for c in command)}")
        _push_import_status()
        return True, 'Import started.'


def start_pinned_imports(entries: List[dict]) -> Tuple[bool, str]:
    """
    Non-interactively import pinned paths.
    """
    global _queue

    if not entries:
        return False, 'No pinned import paths are configured.'

    with _lock:
        if is_import_running():
            return False, 'An import is already running.'

        ok, message = start_import(entries[0]['path'], quiet=True, incremental=entries[0]['incremental'])
        if ok:
            _queue = list(entries[1:])
            for entry in entries:
                if entry.get('watch'):
                    mark_pinned_triggered(entry['id'])
            admin_events.publish('pinned-paths', '')
        return ok, message


def enqueue_pinned_import(entry: dict) -> Tuple[bool, str]:
    """
    Add a pinned path to the non-interactive queue.
    (starts immediately if no other is in the queue)
    """
    global _queue

    with _lock:
        if is_import_running():
            if not any(q['path'] == entry['path'] for q in _queue):
                _queue.append({**entry, 'incremental': True})
            return True, 'Queued.'

        return start_import(entry['path'], quiet=True, incremental=True)


def _start_next_queued() -> None:
    global _queue

    with _lock:
        if not _queue:
            return
        entry = _queue.pop(0)
        ok, message = start_import(entry['path'], quiet=True, incremental=entry['incremental'], reset_log=False)
        if not ok:
            bsn_logger.warning(f"Pinned scan stopped at '{entry['path']}': {message}")
            _queue = []


def _handle_output_chunk(log_file, text: str) -> None:

    global _state

    log_file.write(text)
    log_file.flush()

    if any(s in text for s in _NEEDS_INPUT_SNIPPETS):
        with _lock:
            if _state == 'running':
                _state = 'needs_input'

    _push_import_status()


def _finish_import(log_file, exit_code: int) -> None:
    global _state, _exit_code

    with _lock:
        _exit_code = exit_code
        _state = 'completed' if exit_code == 0 else 'failed'

    log_file.write(f'\n[beetstreamnext] Beets import finished (exit code {exit_code}).\n')
    log_file.flush()

    _push_import_status()
    bsn_logger.info(f'Beets import finished (exit {exit_code})')

    _start_next_queued()


def _pump_output_posix(proc: subprocess.Popen, master_fd: int) -> None:
    with open(BEETS_IMPORT_LOG_PATH, 'a', encoding='utf-8') as log_file:
        try:
            while True:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                _handle_output_chunk(log_file, chunk.decode('utf-8', errors='replace'))
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass
            _finish_import(log_file, proc.wait())


def _pump_output_windows(proc) -> None:
    with open(BEETS_IMPORT_LOG_PATH, 'a', encoding='utf-8') as log_file:
        try:
            while True:
                try:
                    text = proc.read(65536)
                except EOFError:
                    break
                if not text:
                    break
                _handle_output_chunk(log_file, text)
        finally:
            try:
                exit_code = proc.exitstatus
            except Exception:
                exit_code = None
            _finish_import(log_file, 0 if exit_code is None else exit_code)


def send_import_input(text: str) -> Tuple[bool, str]:

    global _state

    with _lock:
        if _proc is None or _state not in ('running', 'needs_input'):
            return False, 'No import is waiting for input.'
        proc = _proc
        fd = _master_fd

    try:
        if IS_WINDOWS:
            proc.write(text + '\r\n')
        else:
            os.write(fd, (text + '\n').encode('utf-8'))
    except (OSError, EOFError) as e:
        bsn_logger.warning(f'Failed to write to beets import stdin: {e}')
        return False, 'Failed to send input.'

    with _lock:
        if _state == 'needs_input':
            _state = 'running'   # Assume this answers whatever prompt was up

    _push_import_status()
    return True, 'Sent.'


def commit_likes(subsonic_id: str, key: str, value: Any) -> None:
    """
    Apply one user's Likes/Rating value to Beets's db
    (only one user can be applied because Beets is single-user)

    Note: non-song and non-album (artists, playlists, radios, podcasts)
     have no row in to attach a value to, so they are silently skipped.
    """

    from beetsplug.beetstreamnext.core.resolve import Resolve

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


def read_config() -> dict:
    path = config_path()
    try:
        content = path.read_text(encoding='utf-8')
    except OSError:
        content = ''

    return {
        'path': str(path),
        'content': content,
        'read_only': not is_writable(path),
        'loaded_at': time.time(),
    }


def write_config(content: str) -> Tuple[bool, str]:
    path = config_path()

    if not is_writable(path):
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

    return True, 'Saved.'


def write_beets_field(
    entity_type: str,
    entity_id: int,
    key: str,
    value: Any,
    allow_flex: bool = False,
) -> None:
    """
    Writes a field in the beets database.
    """

    if entity_type not in ('item', 'album'):
        raise ValueError("entity_type must be 'item' or 'album'")

    if not isinstance(key, str) or not ALPHANUM_CHARS.match(key):
        raise ValueError(f'Invalid field name: {key!r}')

    entity_id = int(entity_id)

    core_table = 'items' if entity_type == 'item' else 'albums'
    attr_table = f'{entity_type}_attributes'

    db = dual_database()

    if key in get_beets_schema(core_table):
        cur = db.execute(
            f"""
            UPDATE beets.{core_table} 
            SET {key} = ? 
            WHERE id = ?
            """, (value, entity_id),
        )
        db.commit()

        # If that worked but changed 0 rows (wrong ID), user should know
        if cur.rowcount == 0:
            bsn_logger.warning(f'No beets {entity_type} found with ID {entity_id}')
        return

    if not allow_flex:
        raise ValueError(
            f"'{key}' is not a column of beets.{core_table}. "
            f"Pass allow_flex=True to write it as a flexible attribute."
        )

    db.execute(
        f"""
        INSERT INTO beets.{attr_table} (entity_id, key, value)
        VALUES (?, ?, ?)
        ON CONFLICT(entity_id, key) DO UPDATE SET value = excluded.value
        """,
        (entity_id, key, str(value)),
    )
    db.commit()
