from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Optional, Tuple, Any, List
import beets
import confuse
import yaml

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import BEETS_IMPORT_LOG_PATH, ALPHANUM_CHARS, DEFAULT_CONFIG_PATH
from beetsplug.beetstreamnext.core.storage.connection import dual_database
from beetsplug.beetstreamnext.utils.db import get_beets_schema
from beetsplug.beetstreamnext.core.services.events import admin_events
from beetsplug.beetstreamnext.core.library.import_paths import mark_pinned_triggered, validate_pinned_path
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.notifications import (
    IMPORT_COMPLETED, IMPORT_FAILED, IMPORT_NEEDS_INPUT, IMPORT_QUEUE_ABORTED, IMPORT_STARTED, notify
)
from beetsplug.beetstreamnext.core.config.settings_schema import SETTINGS_SCHEMA
from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.core.library.beets.config_policy import (
    CODE_EXECUTION_PLUGINS, CODE_EXECUTION_KEYS, COMMAND_OPTIONS, DISK_WRITING_PLUGINS,
)
from beetsplug.beetstreamnext.utils.ansi import ansi_to_html
from beetsplug.beetstreamnext.utils.general import subprocess_env
from beetsplug.beetstreamnext.utils.text import format_elapsed
from beetsplug.beetstreamnext.utils.system import is_writable, read_log

IS_WINDOWS = sys.platform == 'win32'

if IS_WINDOWS:
    from winpty import PtyProcess
else:
    import pty

IMPORT_PUSH_INTERVAL = 0.5     # max seconds between live import log updates

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
        if any(beets.config['import'][key].get(bool) for key in ('move', 'copy', 'write')):
            return False

        loaded = beets.config['plugins'].as_str_seq()

        for name, option in DISK_WRITING_PLUGINS.items():
            if name not in loaded:
                continue
            if option is None or beets.config[name][option].get(bool):
                return False

    except confuse.ConfigError:
        return False

    return True


def htmlify_log() -> List[str]:
    """Import log as HTML lines (escaped, ANSI converted)."""
    return [ansi_to_html(line) for line in read_log(BEETS_IMPORT_LOG_PATH, drop_python=True)]


class ImportSession:
    """
    Manages the `beet import` subprocess, the queue of pinned paths
    and the status pushes to admin sessions.
    """

    def __init__(self) -> None:

        self._lock = threading.RLock()
        self._push_lock = threading.Lock()

        self._last_push = 0.0
        self._push_timer: Optional[threading.Timer] = None

        self._proc: Optional[Any] = None         # subprocess.Popen or winpty.PtyProcess
        self._master_fd: Optional[int] = None    # POSIX only, Windows pty process reads/writes itself

        self._state: str = 'idle'                # idle, running, needs_input, completed, failed
        self._current_path: Optional[str] = None

        self._exit_code: Optional[int] = None
        self._started_at: Optional[float] = None

        self._queue: List[dict] = []             # pinned paths waiting to be scanned

    def status(self) -> dict:
        """Snapshot of current import state."""
        with self._lock:
            return {'state': self._state, 'path': self._current_path, 'exit_code': self._exit_code}

    def is_running(self) -> bool:
        """Import session is running or waiting on input."""
        return self.status()['state'] in ('running', 'needs_input')

    def _push_status(self, throttle: float = 0.0) -> None:
        """
        Publish import status and recent log to connected admin sessions.
        """
        with self._push_lock:
            wait = self._last_push + throttle - time.monotonic()
            if throttle and wait > 0:
                if self._push_timer is None:
                    self._push_timer = threading.Timer(wait, self._push_status)
                    self._push_timer.daemon = True
                    self._push_timer.start()
                return

            self._last_push = time.monotonic()
            self._push_timer = None

        admin_events.publish('beets-import', {**self.status(), 'lines': htmlify_log()})

    def start(self,
            path: str,
            quiet: bool = False,
            incremental: bool = False,
            reset_log: bool = True
        ) -> Tuple[bool, str]:
        """
        Start a `beet import` against `path`.
        """
        with self._lock:
            if self.is_running():
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

            env = subprocess_env('NO_COLOR')
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

                self._proc = proc
                self._master_fd = None
                pump_thread = threading.Thread(target=self._pump_output_windows, args=(proc,), daemon=True)
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
                    bsn_logger.error(f'Failed to start beets import: {e}')
                    return False, 'Failed to start the import process.'

                finally:
                    os.close(slave_fd)

                self._proc = proc
                self._master_fd = master_fd
                pump_thread = threading.Thread(target=self._pump_output_posix, args=(proc, master_fd), daemon=True)
                pid_for_log = proc.pid

            self._current_path = str(candidate)
            self._state = 'running'
            self._exit_code = None
            self._started_at = time.monotonic()

            pump_thread.start()

            bsn_logger.info(f"Started beets import (pid {pid_for_log}) on '{candidate}': "
                             f"{' '.join(shlex.quote(c) for c in command)}")
            self._push_status()
            notify(IMPORT_STARTED, path=self._current_path)
            return True, 'Import started.'

    def start_pinned(self, entries: List[dict]) -> Tuple[bool, str]:
        """
        Import pinned paths manually.
        """
        if not entries:
            return False, 'No pinned import paths are configured.'

        with self._lock:
            if self.is_running():
                return False, 'An import is already running.'

            ok, message = self.start(entries[0]['path'], quiet=True, incremental=entries[0]['incremental'])
            if ok:
                self._queue = list(entries[1:])
                for entry in entries:
                    if entry.get('watch'):
                        mark_pinned_triggered(entry['id'])
                admin_events.publish('pinned-paths', '')
            return ok, message

    def enqueue_pinned(self, entry: dict) -> Tuple[bool, str]:
        """
        Add a pinned path to the non-interactive queue.
        (starts immediately if no other is in the queue)
        """
        with self._lock:
            if self.is_running():
                if not any(q['path'] == entry['path'] for q in self._queue):
                    self._queue.append({**entry, 'incremental': True})
                return True, 'Queued.'

            return self.start(entry['path'], quiet=True, incremental=True)

    def _start_next_queued(self) -> None:
        with self._lock:
            if not self._queue:
                return
            entry = self._queue.pop(0)
            ok, message = self.start(entry['path'], quiet=True, incremental=entry['incremental'], reset_log=False)
            if not ok:
                bsn_logger.warning(f"Pinned scan stopped at '{entry['path']}': {message}")
                self._queue = []
                notify(IMPORT_QUEUE_ABORTED, path=entry['path'], exit_code='', duration='')

    def _handle_output_chunk(self, log_file, text: str) -> None:
        log_file.write(text)
        log_file.flush()

        needs_input_path = None
        if any(s in text for s in _NEEDS_INPUT_SNIPPETS):
            with self._lock:
                if self._state == 'running':
                    self._state = 'needs_input'
                    needs_input_path = self._current_path

        if needs_input_path is not None:    # Only on the running -> needs_input
            notify(IMPORT_NEEDS_INPUT, path=needs_input_path)

        self._push_status(throttle=0.0 if needs_input_path is not None else IMPORT_PUSH_INTERVAL)

    def _finish(self, log_file, exit_code: int) -> None:
        with self._lock:
            self._exit_code = exit_code
            self._state = 'completed' if exit_code == 0 else 'failed'
            path = self._current_path
            duration = format_elapsed(time.monotonic() - self._started_at) if self._started_at is not None else ''

        log_file.write(f'\n[beetstreamnext] Beets import finished (exit code {exit_code}).\n')
        log_file.flush()

        self._push_status()

        bsn_logger.info(f'Beets import finished (exit {exit_code})')

        notify(IMPORT_COMPLETED if exit_code == 0 else IMPORT_FAILED,
               path=path, exit_code=exit_code, duration=duration)

        self._start_next_queued()

    def _pump_output_posix(self, proc: subprocess.Popen, master_fd: int) -> None:
        with open(BEETS_IMPORT_LOG_PATH, 'a', encoding='utf-8') as log_file:
            try:
                while True:
                    try:
                        chunk = os.read(master_fd, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    self._handle_output_chunk(log_file, chunk.decode('utf-8', errors='replace'))
            finally:
                with self._lock:
                    if self._master_fd == master_fd:
                        self._master_fd = None
                    try:
                        os.close(master_fd)
                    except OSError:
                        pass
                self._finish(log_file, proc.wait())

    def _pump_output_windows(self, proc) -> None:
        with open(BEETS_IMPORT_LOG_PATH, 'a', encoding='utf-8') as log_file:
            try:
                while True:
                    try:
                        text = proc.read(65536)
                    except EOFError:
                        break
                    if not text:
                        break
                    self._handle_output_chunk(log_file, text)
            finally:
                try:
                    exit_code = proc.wait()
                except Exception:
                    exit_code = None

                if exit_code is None:
                    bsn_logger.warning('Could not get the beets import exit status.')
                    exit_code = 1
                self._finish(log_file, exit_code)

    def send_input(self, text: str) -> Tuple[bool, str]:

        with self._lock:
            if (self._proc is None or self._state not in ('running', 'needs_input')
                    or (not IS_WINDOWS and self._master_fd is None)):
                return False, 'No import is waiting for input.'

            try:
                if IS_WINDOWS:
                    self._proc.write(text + '\r\n')
                else:
                    os.write(self._master_fd, (text + '\n').encode('utf-8'))

            except (OSError, EOFError) as e:
                bsn_logger.warning(f'Failed to write to beets import stdin: {e}')
                return False, 'Failed to send input.'

            if self._state == 'needs_input':
                self._state = 'running'   # Assume this answers whatever prompt was up

        self._push_status()
        return True, 'Sent.'


import_session = ImportSession()


def commit_likes(subsonic_id: str, key: str, value: Any) -> None:
    """
    Apply one user's Likes/Rating value to Beets's db
    (only one user can be applied because Beets is single-user)

    Note: non-song and non-album (artists, playlists, radios, podcasts)
     have no row in to attach a value to, so they are silently skipped.
    """

    from beetsplug.beetstreamnext.core.library.resolve import Resolve

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

    edit_allowed = bool(settings_store.get('allow_beets_config_edit'))

    try:
        locked = sorted(_exec_violations(content)) if edit_allowed else []
    except yaml.YAMLError:
        locked = []

    return {
        'path': str(path),
        'content': content,
        'locked': locked,
        'read_only': not edit_allowed or not is_writable(path),
        'edit_disabled': not edit_allowed,
        'settings_file': str(app.config.get('BSN_CONFIG_PATH') or DEFAULT_CONFIG_PATH) if app.config.get('STANDALONE_MODE') else str(path),
        'edit_env_var': SETTINGS_SCHEMA['allow_beets_config_edit']['env_var'],
        'loaded_at': time.time(),
    }


def _exec_violations(content: str) -> dict:
    """
    Config entries in `content` that would let beets run code or commands, as {name: value}.
    Raises yaml.YAMLError on invalid YAML.
    """

    data = yaml.safe_load(content)     # safe_load: no python/object tags

    if data is None:
        return {}
    if not isinstance(data, dict):
        return {'<top-level is not a mapping>': None}

    found = {key: data[key] for key in CODE_EXECUTION_KEYS if key in data}

    plugins = data.get('plugins')
    if isinstance(plugins, str):
        plugins = plugins.split()
    if isinstance(plugins, list):
        found.update({f'plugins: {p}': True for p in plugins if p in CODE_EXECUTION_PLUGINS})

    for plugin, options in COMMAND_OPTIONS.items():
        section = data.get(plugin)
        if isinstance(section, dict):
            found.update({f'{plugin}.{option}': section[option] for option in options if option in section})

    return found


def write_config(content: str) -> Tuple[bool, str]:

    edit_allowed = bool(settings_store.get('allow_beets_config_edit'))

    if not edit_allowed:
        return False, 'Editing the beets config from the admin panel is disabled (see `allow_beets_config_edit`).'

    path = config_path()

    if not is_writable(path):
        return False, 'Config file is read-only.'

    try:
        violations = _exec_violations(content)
    except yaml.YAMLError as e:
        return False, f'Invalid YAML: {e}'

    # Entries the operator already had in the file are trusted as long as they are left unchanged
    try:
        existing = _exec_violations(path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError):
        existing = {}

    violations = {name for name, value in violations.items()
                  if name not in existing or existing[name] != value}

    if violations:
        return False, ('Refused: these settings can execute code, so they cannot be modified '
                       f'from the admin panel: {", ".join(sorted(violations))}')

    target = path.resolve()

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(dir=target.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as tf:
                    tf.write(content)

                if target.exists():
                    shutil.copymode(target, tmp_path)

                    if hasattr(os, 'chown'):
                        stat = target.stat()
                        with suppress(OSError):
                            os.chown(tmp_path, stat.st_uid, stat.st_gid)

                os.replace(tmp_path, target)
            except OSError:
                with suppress(OSError):
                    os.unlink(tmp_path)
                raise
        except OSError:
            # No atomic swap possible
            target.write_text(content, encoding='utf-8')

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
