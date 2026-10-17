from __future__ import annotations

import shutil
import threading
from pathlib import Path

from beetsplug.beetstreamnext.constants import HLS_MAX_CACHE_BYTES, HLS_MAX_CONCURRENT
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.utils.system import dir_size


class HLSSessions:
    """
    Manager for HLS sessions: how many can be prepared, which ones
    ffmpeg is still writing, and keeping cache under the size cap.
    """

    def __init__(self, max_concurrent: int) -> None:
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()

        self._encoding: set[str] = set()    # ids of sessions ffmpeg is still writing

    def try_acquire_slot(self) -> bool:
        return self._slots.acquire(blocking=False)

    def release_slot(self) -> None:
        self._slots.release()

    def end(self, session_id: str) -> None:
        """
        Session's encode is done (or never started), slot can be freed.
        """
        with self._lock:
            self._encoding.discard(session_id)
        self._slots.release()

    def reserve(self, session_dir: Path) -> str:
        """
        Create `session_dir` and mark it: 'created' for being encoded,
        'exists' if it already exists, 'full' if no room can be made for it.
        """
        with self._lock:
            if session_dir.exists():
                return 'exists'

            if not self._enforce_cache_cap(session_dir.parent):
                return 'full'

            session_dir.mkdir(parents=True)
            self._encoding.add(session_dir.name)
            return 'created'

    def _enforce_cache_cap(self, cache_dir: Path) -> bool:
        """
        Delete least recently used sessions until HLS cache is under the size cap.
        """
        total = dir_size(cache_dir)
        if total <= HLS_MAX_CACHE_BYTES:
            return True

        idle = []
        for entry in cache_dir.iterdir():
            if entry.is_dir() and entry.name not in self._encoding:
                idle.append((entry.stat().st_mtime, entry, dir_size(entry)))

        for _, entry, size in sorted(idle, key=lambda item: item[0]):
            if total <= HLS_MAX_CACHE_BYTES:
                break
            shutil.rmtree(entry, ignore_errors=True)
            total -= size
            bsn_logger.debug(f"HLS cache over size cap: removed '{entry.name}'.")

        return total <= HLS_MAX_CACHE_BYTES


hls_sessions = HLSSessions(HLS_MAX_CONCURRENT)
