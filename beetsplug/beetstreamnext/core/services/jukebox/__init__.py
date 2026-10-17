from __future__ import annotations

import threading
from typing import Optional

from beetsplug.beetstreamnext.core.services.jukebox.base import JukeboxBackend
from beetsplug.beetstreamnext.core.services.jukebox.local import LocalJukeboxPlayer
from beetsplug.beetstreamnext.core.services.jukebox.sonos import SonosJukeboxPlayer
from beetsplug.beetstreamnext.core.services.jukebox.chromecast import ChromecastJukeboxPlayer


_JUKEBOX_BACKENDS = {'sonos': SonosJukeboxPlayer, 'chromecast': ChromecastJukeboxPlayer}


class JukeboxManager:
    """
    Lazily creates the jukebox player for the configured backend,
    and swaps it out when the setting changes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._player: Optional[JukeboxBackend] = None
        self._backend: Optional[str] = None

    def player(self) -> JukeboxBackend:
        from beetsplug.beetstreamnext.core.config.store import settings_store
        backend = settings_store.get('jukebox_backend')

        if self._player is None or self._backend != backend:
            with self._lock:
                if self._player is None or self._backend != backend:
                    if self._player is not None:
                        self._player.shutdown()
                    self._player = _JUKEBOX_BACKENDS.get(backend, LocalJukeboxPlayer)()
                    self._backend = backend

        return self._player


jukebox_manager = JukeboxManager()
