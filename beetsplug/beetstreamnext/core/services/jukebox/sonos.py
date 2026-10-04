from __future__ import annotations

from typing import List, Optional

from beetsplug.beetstreamnext.constants import SOCO
from beetsplug.beetstreamnext.core.media.tokeniser import stream_tokeniser
from beetsplug.beetstreamnext.utils.text import parse_duration, format_duration
from beetsplug.beetstreamnext.core.services.jukebox.base import JukeboxBackend, JukeboxUnavailableException


if SOCO:
    import soco.config
    soco.config.REQUEST_TIMEOUT = 20    # Just to give a bit more time to wireless speakers to wake up


def sonos_discovery(timeout: float = 5.0) -> List[dict]:
    """Scan the network for Sonos speakers. Returns [{'name', 'ip', 'uid'}, ...]."""

    if not SOCO:
        raise JukeboxUnavailableException("The 'soco' package isn't installed. Install the 'sonos' extra to use this backend.")

    import soco

    try:
        zones = soco.discover(timeout=timeout) or set()
    except Exception as e:
        raise JukeboxUnavailableException(f'Sonos discovery failed: {e}') from e

    speakers = [{'name': z.player_name, 'ip': z.ip_address, 'uid': z.uid} for z in zones]
    speakers.sort(key=lambda z: z['name'].lower())
    return speakers


class SonosJukeboxPlayer(JukeboxBackend):
    """
    Wrapper around a Sonos speaker, controlled over the network via SoCo.

    Local files, and http(s) URLs without a recognisable audio extension
    are exposed to the speaker as tokenised stream URLs.
    URLs that already end with a known extension can be passed straight through.
    """
    NAME = 'sonos'

    def __init__(self):
        super().__init__()
        self._device = None                        # soco.SoCo connected lazily
        self._device_ip: Optional[str] = None

    def _target(self):
        """The zone that actually accepts transport commands (the group's coordinator)."""
        try:
            group = self._device.group
            return group.coordinator if group else self._device
        except Exception:
            return self._device

    def _is_ready(self) -> bool:
        return self._device is not None

    def _ensure_ready(self) -> None:
        if not SOCO:
            raise JukeboxUnavailableException("The 'soco' package isn't installed. Install the 'sonos' extra to use this backend.")

        from beetsplug.beetstreamnext.core.config.store import settings_store

        ip = settings_store.get('jukebox_hardware_device')
        if not ip:
            raise JukeboxUnavailableException('No Sonos speaker selected. Pick one in the admin panel.')

        if self._device is None or self._device_ip != ip:
            import soco
            self._device = soco.SoCo(ip)
            self._device_ip = ip

    def _live_status(self) -> dict:

        target = self._target()
        transport = target.get_current_transport_info()
        track_info = target.get_current_track_info()
        volume = target.volume

        try:
            current_index = int(track_info.get('playlist_position', '0')) - 1
        except (TypeError, ValueError):
            current_index = -1

        if not (0 <= current_index < len(self._queue)):
            current_index = -1

        playing = current_index >= 0 and transport.get('current_transport_state') in ('PLAYING', 'TRANSITIONING')

        return {
            'currentIndex': current_index,
            'playing': playing,
            'gain': round((volume or 0) / 100.0, 4),
            'position': int(parse_duration(track_info.get('position', '0:00:00'))),
        }

    def _backend_clear(self) -> None:
        try:
            self._target().clear_queue()
        except Exception as e:
            raise JukeboxUnavailableException(f'Failed to clear the Sonos queue: {e}') from e

    def _queue_item(self, entry_id: str, path: str):
        """
        Build the DIDL item to hand to AddURIToQueue.

        Radio stations have no fixed duration, and get rejected as ordinary tracks with UPnP error 804 unless
        they're represented as an audio broadcast with a matching protocol info.
        """
        from soco.data_structures import DidlResource, DidlObject, DidlAudioBroadcast
        from beetsplug.beetstreamnext.core.library.ids import IDs

        uri = self._resolve_uri(path)

        if IDs.decode_type(entry_id) == 'radio':
            res = [DidlResource(uri=uri, protocol_info='http-get:*:audio/mpeg:*')]
            return DidlAudioBroadcast(title='', parent_id='', item_id='', resources=res)

        res = [DidlResource(uri=uri, protocol_info='x-rincon-playlist:*:*:*')]
        return DidlObject(title='', parent_id='', item_id='', resources=res)

    def _backend_append(self, entry_id: str, path: str) -> None:
        try:
            self._target().add_to_queue(self._queue_item(entry_id, path))
        except Exception as e:
            raise JukeboxUnavailableException(f'Failed to queue track on Sonos: {e}') from e

    def _backend_remove(self, index: int) -> None:
        try:
            self._target().remove_from_queue(index)
        except Exception as e:
            raise JukeboxUnavailableException(f'Failed to remove track from the Sonos queue: {e}') from e

    def _backend_play_from(self, index: int) -> None:
        try:
            self._target().play_from_queue(index)
        except Exception as e:
            raise JukeboxUnavailableException(f'Failed to skip on Sonos: {e}') from e

    def _backend_resume(self) -> None:
        try:
            self._target().play()
        except Exception as e:
            raise JukeboxUnavailableException(f'Failed to start Sonos playback: {e}') from e

    def _backend_pause(self) -> None:
        self._target().pause()

    def _backend_seek(self, offset: float) -> None:
        try:
            self._target().seek(format_duration(seconds=offset, force_hms=True))
        except Exception as e:
            raise JukeboxUnavailableException(f'Failed to seek on Sonos: {e}') from e

    def _backend_set_volume(self, gain: float) -> None:
        try:
            self._target().volume = int(gain * 100)
        except Exception as e:
            raise JukeboxUnavailableException(f'Failed to set Sonos volume: {e}') from e

    def _backend_is_playing(self) -> bool:
        return self._target().get_current_transport_info().get('current_transport_state') in ('PLAYING', 'TRANSITIONING')

    def _backend_shutdown(self) -> None:
        if self._device is not None:
            try:
                self._target().pause()
            except Exception:
                pass

        self._device = None
        self._device_ip = None

        stream_tokeniser.clear()
