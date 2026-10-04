from __future__ import annotations

import threading
from typing import Optional
from beetsplug.beetstreamnext.core.services.jukebox.base import JukeboxBackend
from beetsplug.beetstreamnext.core.services.jukebox.local import LocalJukeboxPlayer
from beetsplug.beetstreamnext.core.services.jukebox.sonos import SonosJukeboxPlayer
from beetsplug.beetstreamnext.core.services.jukebox.chromecast import ChromecastJukeboxPlayer


# Lazy instantiation (but with hot-swap)

_JUKEBOX_BACKENDS = {'sonos': SonosJukeboxPlayer, 'chromecast': ChromecastJukeboxPlayer}

_jukebox_player: Optional[JukeboxBackend] = None
_jukebox_backend: Optional[str] = None
_jukebox_player_lock = threading.Lock()


def get_jukebox_player() -> JukeboxBackend | None:

    global _jukebox_player, _jukebox_backend

    from beetsplug.beetstreamnext.core.config.store import settings_store
    backend = settings_store.get('jukebox_backend')

    if _jukebox_player is None or _jukebox_backend != backend:
        with _jukebox_player_lock:
            if _jukebox_player is None or _jukebox_backend != backend:
                if _jukebox_player is not None:
                    _jukebox_player.shutdown()
                _jukebox_player = _JUKEBOX_BACKENDS.get(backend, LocalJukeboxPlayer)()
                _jukebox_backend = backend

    return _jukebox_player
