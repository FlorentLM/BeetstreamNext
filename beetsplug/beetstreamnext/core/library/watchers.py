from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.constants import AUDIO_EXTENSIONS, IMPORTWATCH_POLL_TIME, INDEXWATCH_POLL_TIME, INDEXWATCH_SETTLE
from beetsplug.beetstreamnext.core.services.events import admin_events
from beetsplug.beetstreamnext.core.library.beets.interaction import import_session
from beetsplug.beetstreamnext.core.library.import_paths import list_pinned_paths, mark_pinned_triggered
from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.utils.general import filter_dict
from beetsplug.beetstreamnext.core.library.indexes import Index
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.utils.db import library_cache


Snapshot = frozenset[tuple[str, int, int]]


@dataclass
class PathState:
    snapshot: Snapshot | None = None
    changed_at: float = 0.0
    dirty: bool = False
    last_error: str = ''


def snapshot_audio(root: Path) -> Snapshot:
    """(relative path, size, mtime_ns) of every audio file under root."""
    files = set()
    try:
        for p in root.rglob('*'):
            if p.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            try:
                st = p.stat()
            except OSError:
                continue  # vanished mid-walk
            if p.is_file():
                files.add((str(p.relative_to(root)), st.st_size, st.st_mtime_ns))
    except OSError:
        pass
    return frozenset(files)


class ImportWatcher(threading.Thread):

    def __init__(self) -> None:
        super().__init__(name='import-watcher', daemon=True)
        self._states: dict[str, PathState] = {}

    def run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:
                bsn_logger.error(f'Import watcher error: {e}')

            time.sleep(IMPORTWATCH_POLL_TIME)

    def tick(self) -> None:
        if not settings_store.get('import_watch_enabled'):
            self._states.clear()
            return

        with app.app_context():

            watched = [p for p in list_pinned_paths() if p['watch']]
            self._states = filter_dict(self._states, keep={p['path'] for p in watched})

            settle = settings_store.get('import_watch_settle')
            now = time.monotonic()

            for entry in watched:
                state = self._states.setdefault(entry['path'], PathState())
                snap = snapshot_audio(Path(entry['path']))

                if snap != state.snapshot:
                    state.snapshot = snap
                    state.changed_at = now
                    state.dirty = bool(snap)  # empty folderk, nothing to import

                if not state.dirty or now - state.changed_at < settle:
                    continue

                if import_session.is_running():
                    continue

                ok, message = import_session.enqueue_pinned(entry)
                if ok:
                    state.dirty = False
                    state.last_error = ''
                    mark_pinned_triggered(entry['id'])
                    admin_events.publish('pinned-paths', '')
                    bsn_logger.info(f"Watched path '{entry['path']}' settled, import triggered.")

                elif message != state.last_error:
                    state.last_error = message
                    bsn_logger.warning(f"Watched path '{entry['path']}' not imported: {message}")


class IndexWatcher(threading.Thread):
    """
    Rebuilds the indexes in the background
    (at startup and then when the library stops changing)
    """

    def __init__(self) -> None:
        super().__init__(name='index-watcher', daemon=True)
        self._stamp: tuple = ()
        self._changed_at = float('-inf')    # Build at startup
        self._dirty = True

    def run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as e:
                bsn_logger.error(f'Index watcher error: {e}')

            time.sleep(INDEXWATCH_POLL_TIME)

    @with_app_context
    def tick(self) -> None:
        now = time.monotonic()
        stamp = library_cache.stamp()

        if stamp != self._stamp:
            if self._stamp:
                self._changed_at = now
            self._stamp = stamp
            self._dirty = True

        if not self._dirty or now - self._changed_at < INDEXWATCH_SETTLE or import_session.is_running():
            return

        started = time.monotonic()
        for index in Index.__subclasses__():
            index.get()     # a request already rebuilt it

        self._dirty = False
        bsn_logger.debug(f'Library indexes rebuilt in {time.monotonic() - started:.2f} s')