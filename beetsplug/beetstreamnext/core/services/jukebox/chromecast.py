from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, List, Optional

from beetsplug.beetstreamnext.constants import PYCHROMECAST
from beetsplug.beetstreamnext.public.tokeniser import stream_tokeniser
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.jukebox.base import JukeboxBackend, JukeboxUnavailableException


if TYPE_CHECKING and PYCHROMECAST:
    from pychromecast import Chromecast, CastBrowser


def chromecast_discovery(timeout: float = 5.0) -> List[dict]:
    """Scan the network for Chromecast devices. Returns [{'name', 'host', 'uuid'}, ...]."""

    if not PYCHROMECAST:
        raise JukeboxUnavailableException("The 'pychromecast' package isn't installed. Install the 'chromecast' extra to use this backend.")

    import pychromecast
    import zeroconf

    try:
        zconf = zeroconf.Zeroconf()
        browser = pychromecast.CastBrowser(pychromecast.SimpleCastListener(), zconf)
        browser.start_discovery()
        try:
            time.sleep(timeout)
        finally:
            browser.stop_discovery()
    except Exception as e:
        raise JukeboxUnavailableException(f'Chromecast discovery failed: {e}') from e

    devices = [
        {'name': info.friendly_name or str(uuid), 'host': info.host, 'uuid': str(uuid)}
        for uuid, info in browser.devices.items()
    ]
    devices.sort(key=lambda d: d['name'].lower())
    return devices


class ChromecastJukeboxPlayer(JukeboxBackend):
    """
    Wrapper around a Chromecast device, controlled over the network via pychromecast.
    """
    NAME = 'chromecast'

    def __init__(self):
        super().__init__()
        self._device: Optional['Chromecast'] = None
        self._browser: Optional['CastBrowser'] = None
        self._device_target: Optional[str] = None
        self._current_index: int = -1

    def _disconnect_device(self) -> None:
        if self._device is not None:
            try:
                self._device.disconnect(timeout=2)
            except Exception:
                pass
        if self._browser is not None:
            try:
                self._browser.stop_discovery()
            except Exception:
                pass
        self._device = None
        self._browser = None

    def _is_ready(self) -> bool:
        return (
            self._device is not None
            and self._device.socket_client.is_alive()
            and self._device.socket_client.is_connected
        )

    def _ensure_ready(self) -> None:
        if not PYCHROMECAST:
            raise JukeboxUnavailableException("The 'pychromecast' package isn't installed. Install the 'chromecast' extra to use this backend.")

        from beetsplug.beetstreamnext.config.store import settings_store

        target = settings_store.get('jukebox_hardware_device')
        if not target:
            raise JukeboxUnavailableException('No Chromecast selected. Pick one in the admin panel.')

        if self._is_ready() and self._device_target == target:
            return

        import pychromecast
        import zeroconf
        from uuid import UUID

        self._disconnect_device()

        try:
            target_uuid = UUID(target)
        except ValueError:
            target_uuid = None

        try:
            if target_uuid is not None:
                devices, browser = pychromecast.get_listed_chromecasts(uuids=[target_uuid], discovery_timeout=10)
            else:
                # Not a UUID -> treat it as an IP/hostname and connect directly (bypassing mDNS)
                found = threading.Event()
                matched = {}

                def add_callback(uuid, _service):
                    info = browser.devices.get(uuid)
                    if info is not None and info.host == target:
                        matched['info'] = info
                        found.set()

                zconf = zeroconf.Zeroconf()
                browser = pychromecast.CastBrowser(pychromecast.SimpleCastListener(add_callback), zconf, known_hosts=[target])
                browser.start_discovery()
                found.wait(timeout=10)
                devices = [pychromecast.get_chromecast_from_cast_info(matched['info'], zconf)] if 'info' in matched else []
        except Exception as e:
            raise JukeboxUnavailableException(f'Chromecast discovery failed: {e}') from e

        if not devices:
            browser.stop_discovery()
            raise JukeboxUnavailableException(f'Chromecast {target} not found on the network.')

        device = devices[0]
        try:
            device.wait(timeout=10)
        except Exception as e:
            browser.stop_discovery()
            raise JukeboxUnavailableException(f'Failed to connect to Chromecast: {e}') from e

        self._device = device
        self._browser = browser
        self._device_target = target
        self._current_index = -1

    def _live_status(self) -> dict:
        status = self._device.media_controller.status
        volume = self._device.status.volume_level if self._device.status else 1.0

        current_index = self._current_index if 0 <= self._current_index < len(self._queue) else -1
        playing = current_index >= 0 and status.player_is_playing

        return {
            'currentIndex': current_index,
            'playing': playing,
            'gain': round(volume or 0, 4),
            'position': int(status.adjusted_current_time or 0),
        }

    def _backend_clear(self) -> None:
        try:
            self._device.media_controller.stop()
        except Exception:
            pass  # Nothing was loaded, nothing to stop
        self._current_index = -1

    def _backend_append(self, entry_id: str, path: str) -> None:
        pass  # No native queue in Chromecasts. self._queue is the only queue

    def _backend_remove(self, index: int) -> None:
        # self._queue shrinks, current track pointer must follow
        if index < self._current_index:
            self._current_index -= 1
        elif index == self._current_index:
            self._current_index = -1

    @staticmethod
    def _cast_metadata(entry_id: str) -> dict:
        """Metadata and cover art for the Chromecast 'now playing' screen."""

        from pychromecast.controllers.media import METADATA_TYPE_MUSICTRACK
        from beetsplug.beetstreamnext.core.library.serialise import Serialise
        from beetsplug.beetstreamnext.core.media.images import tokenised_image_url

        try:
            entry = Serialise.playable(entry_id) or {}
        except Exception:
            entry = {}

        thumb = None
        cover_art_id = entry.get('coverArt')
        if cover_art_id:
            try:
                thumb = tokenised_image_url(cover_art_id, size=500)
            except Exception:
                thumb = None

        return {
            'title': entry.get('title') or entry.get('name') or None,
            'thumb': thumb,
            'metadata': {
                'metadataType': METADATA_TYPE_MUSICTRACK,
                'artist': entry.get('artist') or '',
                'albumName': entry.get('album') or '',
            },
        }

    def _backend_play_from(self, index: int) -> None:

        from beetsplug.beetstreamnext.core.library.ids import IDs

        entry_id, path = self._queue[index]
        uri = self._resolve_uri(path)
        content_type = self._content_type(path)
        stream_type = 'LIVE' if IDs.decode_type(entry_id) == 'radio' else 'BUFFERED'
        cast_meta = self._cast_metadata(entry_id)

        bsn_logger.info(f'Jukebox ({self.NAME}): loading {uri!r} ({content_type})')

        mc = self._device.media_controller
        receiver = self._device.socket_client.receiver_controller

        # Force a fresh status so the cache is accurate before play_media()
        refreshed = threading.Event()
        receiver.update_status(callback_function=lambda *_: refreshed.set())
        refreshed.wait(timeout=5)

        try:
            mc.play_media(
                uri, content_type, autoplay=True, stream_type=stream_type,
                title=cast_meta['title'], thumb=cast_meta['thumb'], metadata=cast_meta['metadata'],
            )
        except Exception as e:
            raise JukeboxUnavailableException(f'Failed to play on Chromecast: {e}') from e

        for _ in range(100):
            if mc.status.content_id == uri:
                break
            time.sleep(0.1)
        else:
            raise JukeboxUnavailableException(f"Chromecast didn't confirm loading the track.")

        self._current_index = index

    def _backend_resume(self) -> None:
        try:
            self._device.media_controller.play()
        except Exception as e:
            raise JukeboxUnavailableException(f'Failed to start Chromecast playback: {e}') from e

    def _backend_pause(self) -> None:
        try:
            self._device.media_controller.pause()
        except Exception as e:
            raise JukeboxUnavailableException(f'Failed to pause Chromecast: {e}') from e

    def _backend_seek(self, offset: float) -> None:
        try:
            self._device.media_controller.seek(offset)
        except Exception as e:
            raise JukeboxUnavailableException(f'Failed to seek on Chromecast: {e}') from e

    def _backend_set_volume(self, gain: float) -> None:
        try:
            self._device.set_volume(gain)
        except Exception as e:
            raise JukeboxUnavailableException(f'Failed to set Chromecast volume: {e}') from e

    def _backend_is_playing(self) -> bool:
        return self._device.media_controller.status.player_is_playing

    def _backend_shutdown(self) -> None:
        if self._device is not None:
            try:
                self._device.media_controller.stop()
            except Exception:
                pass

        self._disconnect_device()
        self._device_target = None
        self._current_index = -1

        stream_tokeniser.clear()
