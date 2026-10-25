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
from typing import Optional, Tuple, Any, List, Dict
import beets
import confuse
import yaml

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import (
    BEETS_IMPORT_LOG_PATH, BEETS_IMPORT_RESULTS_PATH, ALPHANUM_CHARS, DEFAULT_CONFIG_PATH
)
from beetsplug.beetstreamnext.core.storage.connection import dual_database
from beetsplug.beetstreamnext.utils.db import get_beets_schema
from beetsplug.beetstreamnext.core.services.events import admin_events
from beetsplug.beetstreamnext.core.library.beets import import_results
from beetsplug.beetstreamnext.core.library import import_history
from beetsplug.beetstreamnext.core.library.import_paths import mark_pinned_triggered, validate_pinned_path
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.notifications import notify
from beetsplug.beetstreamnext.core.config.settings_schema import SETTINGS_SCHEMA
from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.core.library.beets.config_policy import (
    CODE_EXECUTION_PLUGINS, CODE_EXECUTION_KEYS, COMMAND_OPTIONS, DISK_WRITING_PLUGINS,
)
from beetsplug.beetstreamnext.utils.ansi import log_line
from beetsplug.beetstreamnext.utils.general import subprocess_env
from beetsplug.beetstreamnext.utils.text import format_elapsed
from beetsplug.beetstreamnext.utils.system import is_writable, read_log
from beetsplug.beetstreamnext_events import EVENTS_ENV_VAR, RETRY_ENV_VAR

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
    return [log_line(line) for line in read_log(BEETS_IMPORT_LOG_PATH, drop_python=True)]


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

            reporting = import_results.reset(BEETS_IMPORT_RESULTS_PATH)

            if reporting:
                try:
                    plugins = [p for p in beets.config['plugins'].as_str_seq() if p != 'beetstreamnext']
                except confuse.ConfigError:
                    reporting = False
                else:
                    command += ['-p', ','.join([*plugins, import_results.EVENTS_PLUGIN])]

            config_path = app.config.get('BEETS_CONFIG_PATH')
            if config_path:
                command += ['-c', str(config_path)]

            command += ['-l', library_path, 'import']
            if quiet:
                command.append('-q')

            # Always record skipped (or imported) folders no matter what `incremental_skip_later` says
            if quiet and not incremental:
                command.append('--noincremental')       # Pinned path: non-incremental
            else:
                command += ['--incremental', '--noincremental-skip-later']
            command.append(str(candidate))

            env = subprocess_env('NO_COLOR')
            env.setdefault('TERM', 'xterm-256color')
            if reporting:
                env[EVENTS_ENV_VAR] = str(BEETS_IMPORT_RESULTS_PATH)
            if not quiet:     # Manual imports: force no skip (companion plugin makes beets forget it)
                env[RETRY_ENV_VAR] = '1'

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
            notify('import_started', path=self._current_path)
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

    def enqueue(self, path: str, quiet: bool = True, incremental: bool = False) -> Tuple[bool, str]:
        """
        Queue `path` (or start import immediately if queue empty).
        """
        with self._lock:
            if self.is_running():
                if path != self._current_path and not any(q['path'] == path for q in self._queue):
                    self._queue.append({'path': path, 'incremental': incremental, 'quiet': quiet})
                return True, 'Queued.'

            return self.start(path, quiet=quiet, incremental=incremental)

    def _start_next_queued(self) -> None:
        with self._lock:
            if not self._queue:
                return
            entry = self._queue.pop(0)
            ok, message = self.start(
                entry['path'],
                quiet=entry.get('quiet', True),
                incremental=entry['incremental'],
                reset_log=False
            )

            if not ok:
                bsn_logger.warning(f"Pinned scan stopped at '{entry['path']}': {message}")
                self._queue = []
                notify('import_queue_aborted', path=entry['path'], exit_code='', duration='')

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
            notify('import_needs_input', path=needs_input_path)

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

        event = 'import_completed' if exit_code == 0 else 'import_failed'

        results = import_results.read_results(BEETS_IMPORT_RESULTS_PATH)
        results_summary = import_results.summarize(results)

        if import_history.record_run(path, results):
            admin_events.publish('import-history', '')

        notify(event, path=path, exit_code=exit_code, duration=duration, **results_summary)

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
        locked = sorted(_exec_entries(content)) if edit_allowed else []
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


def _line_of(root: Optional[yaml.Node], *path: str) -> int | None:
    """line of the deepest entry in `path` in the YAML."""

    line = None
    node = root

    for name in path:
        if not isinstance(node, yaml.MappingNode):
            break

        # Last one wins, like in the loaded data
        match = None
        for key, value in node.value:
            if key.value == name:
                match = (key, value)

        if match is None:
            break

        key, node = match
        line = key.start_mark.line + 1

    return line


def _exec_entries(content: str) -> Dict[str, Tuple[Any, Optional[int]]]:
    """
    Config entries in `content` that would let beets run code or commands, as {name: (value, line)}.
    Raises yaml.YAMLError on invalid YAML.
    """

    loader = yaml.SafeLoader(content)

    try:
        root = loader.get_single_node()
        data = loader.construct_document(root) if root is not None else None
    finally:
        loader.dispose()

    if data is None:
        return {}

    if not isinstance(data, dict):
        return {'<top-level is not a mapping>': (None, 1)}

    # name: (value, path to it in the YAML)
    found: Dict[str, Tuple[Any, Tuple[str, ...]]] = {}

    for key in CODE_EXECUTION_KEYS:
        if key in data:
            found[key] = (data[key], (key,))

    plugins = data.get('plugins')

    if isinstance(plugins, str):
        plugins = plugins.split()

    if isinstance(plugins, list):
        for plugin in plugins:
            if isinstance(plugin, str) and plugin in CODE_EXECUTION_PLUGINS:
                found[f'plugins: {plugin}'] = (True, ('plugins',))

    for plugin, options in COMMAND_OPTIONS.items():
        section = data.get(plugin)

        if not isinstance(section, dict):
            continue

        for option in options:
            if option in section:
                found[f'{plugin}.{option}'] = (section[option], (plugin, option))

    entries = {}

    for name, (value, path) in found.items():
        entries[name] = (value, _line_of(root, *path))

    return entries


def _entries_by_trust(content: str) -> Tuple[dict, dict]:
    """
    The dangerous entries of `content` as (new or changed, unchanged), the latter being what the config file
    already has. Those are trusted as long as they are left as they are (and can still be deleted).
    """

    found = _exec_entries(content)
    try:
        existing = _exec_entries(config_path().read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError):
        existing = {}

    unchanged = {name: entry for name, entry in found.items() if name in existing and existing[name][0] == entry[0]}
    return {name: entry for name, entry in found.items() if name not in unchanged}, unchanged


def locked_lines(content: str) -> List[int]:
    """Lines of the dangerous entries that are left unchanged (they can only be deleted)."""

    try:
        return sorted(line for _, line in _entries_by_trust(content)[1].values() if line)
    except yaml.YAMLError:
        return []


def validate_config(content: str) -> Optional[Tuple[str, Optional[int]]]:
    """
    Why `content` can't be saved as the beets config, as (message, line).
    None when it's fine.
    """

    try:
        refused = _entries_by_trust(content)[0]
    except yaml.YAMLError as e:
        mark = getattr(e, 'problem_mark', None)
        if mark is None:
            return f'Invalid YAML: {e}', None
        return f'Invalid YAML at line {mark.line + 1}, column {mark.column + 1}: {e.problem}', mark.line + 1

    if refused:
        return ('Refused: these settings can execute code, so they cannot be modified '
                f'from the admin panel: {", ".join(sorted(refused))}'), min((line for _, line in refused.values() if line), default=None)

    return None


def write_config(content: str) -> Tuple[bool, str]:

    edit_allowed = bool(settings_store.get('allow_beets_config_edit'))

    if not edit_allowed:
        return False, 'Editing the beets config from the admin panel is disabled (setting `allow_beets_config_edit`).'

    path = config_path()

    if not is_writable(path):
        return False, 'Config file is read-only.'

    issue = validate_config(content)
    if issue:
        return False, issue[0]

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
            """, (value, entity_id),  # nosec B608
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
        """,  # nosec B608
        (entity_id, key, str(value)),
    )
    db.commit()
