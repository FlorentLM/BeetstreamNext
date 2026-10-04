from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, Optional, Tuple

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import AUDIO_EXTENSIONS, IMPORTWATCH_POLL_TIME
from beetsplug.beetstreamnext.core.library.beets_interaction import enqueue_pinned_import, is_import_running
from beetsplug.beetstreamnext.core.services.events import admin_events
from beetsplug.beetstreamnext.core.library.import_paths import list_pinned_paths, mark_pinned_triggered
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.config.store import settings_store


Snapshot = FrozenSet[Tuple[str, int, int]]


@dataclass
class PathState:
    snapshot: Optional[Snapshot] = None
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
        self._states: Dict[str, PathState] = {}

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
            self._states = {k: v for k, v in self._states.items() if k in {p['path'] for p in watched}}

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

                if is_import_running():
                    continue

                ok, message = enqueue_pinned_import(entry)
                if ok:
                    state.dirty = False
                    state.last_error = ''
                    mark_pinned_triggered(entry['id'])
                    admin_events.publish('pinned-paths', '')
                    bsn_logger.info(f"Watched path '{entry['path']}' settled, import triggered.")

                elif message != state.last_error:
                    state.last_error = message
                    bsn_logger.warning(f"Watched path '{entry['path']}' not imported: {message}")