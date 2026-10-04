from __future__ import annotations

import random
import re
import threading
from pathlib import Path
from typing import List, Tuple

from beetsplug.beetstreamnext.public.tokeniser import stream_tokeniser
from beetsplug.beetstreamnext.utils.system import get_mimetype, AUDIO_MIMETYPES
from beetsplug.beetstreamnext.utils.general import request_url
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger


_PLAYABLE_URL_EXT = re.compile(r'\.(' + '|'.join(AUDIO_MIMETYPES) + r')(?:$|\?)', re.IGNORECASE)


class JukeboxUnavailableException(Exception):
    """Raised when the jukebox backend can't be started or reached."""


class PropertyUnavailableExceptionException(JukeboxUnavailableException):
    """Internal: an mpv property is momentarily unavailable."""


class JukeboxBackend:
    """
    Queue and action logic share between jukebox backends.
    """
    NAME = 'jukebox'

    def __init__(self):
        self._lock = threading.RLock()
        self._queue: List[Tuple[str, str]] = []   # (id, local path or playable URL)

    def _resolve_uri(self, path: str) -> str:
        """
        Local files and URLs without a recognisable audio extension are exposed as tokenised stream URLs,
        and URLs ending with a known extension can be passed straight through.
        """

        is_url = path.startswith(('http://', 'https://'))
        if is_url and _PLAYABLE_URL_EXT.search(path):
            return path

        import flask

        token = stream_tokeniser.register(path)

        filename = (Path(path).name if not is_url else '') or 'stream.mp3'
        path_part = flask.url_for('public.tokenised_stream', token=token, filename=filename)

        return request_url(path_part)

    def _content_type(self, path: str) -> str:
        """Best-effort mimetype sniffing (defaults to audio/mpeg)."""
        match = _PLAYABLE_URL_EXT.search(path)
        return get_mimetype(match.group(1) if match else 'mp3')

    def _is_ready(self) -> bool:
        """Quick check (no erroring): is there a live connection to the backend?"""
        raise NotImplementedError

    def _ensure_ready(self) -> None:
        """Make sure the backend is reachable, (re)connecting or (re)starting it if necessary."""
        raise NotImplementedError

    def _live_status(self) -> dict:
        raise NotImplementedError

    def _backend_clear(self) -> None:
        raise NotImplementedError

    def _backend_append(self, entry_id: str, path: str) -> None:
        raise NotImplementedError

    def _backend_remove(self, index: int) -> None:
        raise NotImplementedError

    def _backend_play_from(self, index: int) -> None:
        raise NotImplementedError

    def _backend_resume(self) -> None:
        raise NotImplementedError

    def _backend_pause(self) -> None:
        raise NotImplementedError

    def _backend_seek(self, offset: float) -> None:
        raise NotImplementedError

    def _backend_set_volume(self, gain: float) -> None:
        """Gain is already clamped to [0, 1]."""
        raise NotImplementedError

    def _backend_is_playing(self) -> bool:
        raise NotImplementedError

    def _backend_shutdown(self) -> None:
        raise NotImplementedError

    def track_ids(self) -> List[str]:
        with self._lock:
            return [eid for eid, _ in self._queue]

    def status(self) -> dict:
        empty_status = {'currentIndex': -1, 'playing': False, 'gain': 1.0, 'position': 0}

        with self._lock:
            if not self._is_ready() and not self._queue:
                return empty_status
            try:
                return self._live_status()
            except Exception:
                return empty_status

    def set_playlist(self, entries: List[Tuple[str, str]]) -> None:
        """Replace the queue and start playing from the first track."""

        with self._lock:
            self._ensure_ready()
            self._backend_clear()

            for entry_id, path in entries:
                self._backend_append(entry_id, path)

            self._queue = list(entries)
            if entries:
                self._backend_play_from(0)

    def add(self, entries: List[Tuple[str, str]]) -> None:
        with self._lock:
            self._ensure_ready()
            for entry_id, path in entries:
                self._backend_append(entry_id, path)
            self._queue.extend(entries)

    def clear(self) -> None:

        with self._lock:
            if self._is_ready():
                try:
                    self._backend_clear()
                except Exception as e:
                    bsn_logger.warning(f'Jukebox ({self.NAME}): failed to clear queue: {e}')
            self._queue = []

    def remove(self, index: int) -> None:

        with self._lock:
            if not (0 <= index < len(self._queue)):
                return

            self._ensure_ready()
            self._backend_remove(index)
            del self._queue[index]

    def shuffle(self) -> None:
        with self._lock:
            if not self._queue:
                return
            self._ensure_ready()
            was_playing = self._backend_is_playing()
            random.shuffle(self._queue)
            self._backend_clear()
            for entry_id, path in self._queue:
                self._backend_append(entry_id, path)
            if was_playing:
                self._backend_play_from(0)
            else:
                try:
                    self._backend_pause()
                except Exception as e:
                    bsn_logger.warning(f'Jukebox ({self.NAME}): failed to pause after shuffle: {e}')

    def start(self) -> None:
        with self._lock:
            if not self._queue:
                return
            self._ensure_ready()
            self._backend_resume()

    def stop(self) -> None:
        with self._lock:
            if not self._is_ready():
                return
            try:
                self._backend_pause()
            except Exception as e:
                bsn_logger.warning(f'Jukebox ({self.NAME}): failed to pause: {e}')

    def skip(self, index: int, offset: float = 0.0) -> None:

        with self._lock:
            if not (0 <= index < len(self._queue)):
                raise ValueError('index out of range')

            self._ensure_ready()
            bsn_logger.info(f'Jukebox ({self.NAME}): skip -> index {index} (offset {offset}s)')

            self._backend_play_from(index)
            if offset:
                self._backend_seek(offset)

    def set_gain(self, gain: float) -> None:
        with self._lock:
            self._ensure_ready()
            self._backend_set_volume(max(0.0, min(1.0, gain)))

    def shutdown(self) -> None:
        with self._lock:
            self._backend_shutdown()
            self._queue = []
