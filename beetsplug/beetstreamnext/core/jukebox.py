import itertools
import json
import os
import random
import re
import shlex
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

from beetsplug.beetstreamnext.constants import JUKEBOX_SOCK_DIR, SOCO, PYCHROMECAST
from beetsplug.beetstreamnext.public.tokeniser import stream_tokeniser
from beetsplug.beetstreamnext.utils.system import find_mpv, get_mimetype, AUDIO_MIMETYPES
from beetsplug.beetstreamnext.utils.text import parse_duration, format_duration
from beetsplug.beetstreamnext.utils.general import external_url
from beetsplug.beetstreamnext.core.logging import bsn_logger

if TYPE_CHECKING and PYCHROMECAST:
    from pychromecast import Chromecast, CastBrowser


if SOCO:
    import soco.config
    soco.config.REQUEST_TIMEOUT = 20    # Just to give a bit more time to wireless speakers to wake up



_PLAYABLE_URL_EXT = re.compile(r'\.(' + '|'.join(AUDIO_MIMETYPES) + r')(?:$|\?)', re.IGNORECASE)



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


_MPV_DEVICE_RE = re.compile(r"^'([^']+)'\s*\(([^)]*)\)$")


def mpv_discovery(timeout: float = 5.0) -> List[dict]:
    """List the audio output devices mpv can see on this machine. Returns [{'name', 'device'}, ...]."""

    mpv_bin = find_mpv()
    if not mpv_bin:
        raise JukeboxUnavailableException("mpv wasn't found. Install it, or update the 'mpv_path' setting.")

    try:
        result = subprocess.run([mpv_bin, '--audio-device=help'], capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        raise JukeboxUnavailableException(f'mpv device listing failed: {e}') from e

    devices = []
    for line in result.stdout.splitlines():
        match = _MPV_DEVICE_RE.match(line.strip())
        if not match:
            continue
        device, description = match.groups()
        if device == 'auto':
            continue
        devices.append({'name': description or device, 'device': device})

    devices.sort(key=lambda d: d['name'].lower())
    return devices


##
# Jukebox classes


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

        return external_url(path_part)

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


class LocalJukeboxPlayer(JukeboxBackend):
    """
    Wrapper around a local mpv process controlled over its json IPC socket.
    """
    NAME = 'server_hardware'

    def __init__(self):
        super().__init__()
        self._proc: Optional[subprocess.Popen] = None
        self._sock: Optional[socket.socket] = None
        self._sock_file = None
        self._sock_path: Optional[Path] = None
        self._req_ids = itertools.count(1)

    def _is_ready(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and self._sock is not None

    def _ensure_ready(self):
        if self._is_ready():
            return

        if self._proc is not None:
            bsn_logger.warning(
                f'Jukebox: mpv is not running (last exit code: {self._proc.poll()}). (re)starting it.'
            )

        mpv_bin = find_mpv()
        if not mpv_bin:
            raise JukeboxUnavailableException("mpv wasn't found. Install it, or update the 'mpv_path' setting.")

        from beetsplug.beetstreamnext.settings import settings_store

        JUKEBOX_SOCK_DIR.mkdir(parents=True, exist_ok=True)
        sock_path = JUKEBOX_SOCK_DIR / f'mpv-{os.getpid()}.sock'
        if sock_path.exists():
            sock_path.unlink()

        args = [
            mpv_bin, '--no-video', '--idle=yes', '--msg-level=all=warn',
            f'--input-ipc-server={sock_path}',
        ]
        audio_device = settings_store.get('jukebox_hardware_device')
        if audio_device:
            args.append(f'--audio-device={audio_device}')

        bsn_logger.info(f'Jukebox: launching mpv: {shlex.join(args)}')
        self._proc = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        threading.Thread(target=self._drain_output, args=(self._proc,), daemon=True).start()

        for _ in range(50):
            if sock_path.exists():
                break
            time.sleep(0.1)
        else:
            self._terminate()
            raise JukeboxUnavailableException("mpv didn't open its IPC socket in time.")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(sock_path))
        self._sock = sock
        self._sock_file = sock.makefile('rwb')
        self._sock_path = sock_path
        self._queue = []

    @staticmethod
    def _drain_output(proc: subprocess.Popen):
        """Forward mpv's stdout/stderr into our log."""
        for raw_line in proc.stdout:
            line = raw_line.decode('utf-8', errors='replace').rstrip()
            if line:
                bsn_logger.warning(f'Jukebox: [server_hardware] {line}')

    def _terminate(self):
        if self._sock_file:
            try:
                self._sock_file.close()
            except OSError:
                pass
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

        self._proc = None
        self._sock = None
        self._sock_file = None

    def _command(self, *args):
        """Send a mpv IPC command and return its 'data' field."""

        self._ensure_ready()

        req_id = next(self._req_ids)

        try:
            self._sock_file.write((json.dumps({'command': list(args), 'request_id': req_id}) + '\n').encode('utf-8'))
            self._sock_file.flush()

            while True:
                line = self._sock_file.readline()
                if not line:
                    raise JukeboxUnavailableException('mpv closed its IPC connection.')

                msg = json.loads(line)
                if msg.get('request_id') == req_id:
                    err = msg.get('error')

                    if err != 'success':
                        if err == 'property unavailable':
                            raise PropertyUnavailableExceptionException(f"mpv property unavailable: {args!r}")
                        raise JukeboxUnavailableException(f"mpv command {args!r} failed: {err}")

                    return msg.get('data')

                # else: discard

        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            self._terminate()
            raise JukeboxUnavailableException(f'Lost connection to mpv: {e}') from e

    def _get(self, prop, default=None):
        try:
            return self._command('get_property', prop)
        except JukeboxUnavailableException:
            return default

    def _loadfile(self, path: str, mode: str):
        bsn_logger.info(f'Jukebox: loadfile {mode} -> {path}')
        self._command('loadfile', path, mode)

    def _live_status(self) -> dict:
        playlist_pos = self._get('playlist-pos', -1)
        if playlist_pos is None:
            playlist_pos = -1

        paused = bool(self._get('pause', True))
        volume = self._get('volume', 100.0) or 100.0
        position = self._get('time-pos', 0) or 0

        return {
            'currentIndex': playlist_pos,
            'playing': not paused and playlist_pos >= 0,
            'gain': round(volume / 100.0, 4),
            'position': int(position),
        }

    def _backend_clear(self):
        self._command('stop')
        self._command('playlist-clear')

    def _backend_append(self, entry_id: str, path: str):
        self._loadfile(path, 'append')

    def _backend_remove(self, index: int):
        self._command('playlist-remove', index)

    def _backend_play_from(self, index: int):
        self._command('set_property', 'playlist-pos', index)
        self._command('set_property', 'pause', False)

    def _backend_resume(self):
        self._command('set_property', 'pause', False)

    def _backend_pause(self):
        self._command('set_property', 'pause', True)

    def _backend_seek(self, offset: float):
        # 'time-pos' is sometimes briefly unavailable while mpv is loading the next track
        for _ in range(20):
            try:
                self._command('set_property', 'time-pos', offset)
                return
            except PropertyUnavailableExceptionException:
                time.sleep(0.05)
        bsn_logger.warning(f'Jukebox: could not seek to offset {offset}s after skip.')

    def _backend_set_volume(self, gain: float):
        self._command('set_property', 'volume', gain * 100.0)

    def _backend_is_playing(self) -> bool:
        return not bool(self._get('pause', True))

    def _backend_shutdown(self):
        self._terminate()


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

        from beetsplug.beetstreamnext.settings import settings_store

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
        from beetsplug.beetstreamnext.core.mappings import IDs

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


class ChromecastJukeboxPlayer(JukeboxBackend):
    """
    Wrapper around a Chromecast device, controlled over the network via pychromecast.
    """
    NAME = 'chromecast'

    def __init__(self):
        super().__init__()
        self._device: Optional['Chromecast'] = None
        self._browser: Optional['CastBrowser'] = None
        self._device_uuid: Optional[str] = None
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

        from beetsplug.beetstreamnext.settings import settings_store

        uuid_str = settings_store.get('jukebox_hardware_device')
        if not uuid_str:
            raise JukeboxUnavailableException('No Chromecast selected. Pick one in the admin panel.')

        if self._is_ready() and self._device_uuid == uuid_str:
            return

        import pychromecast
        from uuid import UUID

        try:
            target_uuid = UUID(uuid_str)
        except ValueError as e:
            raise JukeboxUnavailableException(f'Invalid Chromecast UUID: {uuid_str!r}') from e

        self._disconnect_device()

        try:
            devices, browser = pychromecast.get_listed_chromecasts(uuids=[target_uuid], discovery_timeout=10)
        except Exception as e:
            raise JukeboxUnavailableException(f'Chromecast discovery failed: {e}') from e

        if not devices:
            browser.stop_discovery()
            raise JukeboxUnavailableException(f'Chromecast {uuid_str} not found on the network.')

        device = devices[0]
        try:
            device.wait(timeout=10)
        except Exception as e:
            browser.stop_discovery()
            raise JukeboxUnavailableException(f'Failed to connect to Chromecast: {e}') from e

        self._device = device
        self._browser = browser
        self._device_uuid = uuid_str
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
        from beetsplug.beetstreamnext.core.mappings import Serialise
        from beetsplug.beetstreamnext.core.images import tokenised_image_url

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
        from beetsplug.beetstreamnext.core.mappings import IDs

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
        self._device_uuid = None
        self._current_index = -1

        stream_tokeniser.clear()


##
# Lazy instantiation (but with hot-swap)

_JUKEBOX_BACKENDS = {'sonos': SonosJukeboxPlayer, 'chromecast': ChromecastJukeboxPlayer}

_jukebox_player: Optional[JukeboxBackend] = None
_jukebox_backend: Optional[str] = None
_jukebox_player_lock = threading.Lock()


def get_jukebox_player() -> JukeboxBackend | None:

    global _jukebox_player, _jukebox_backend

    from beetsplug.beetstreamnext.settings import settings_store
    backend = settings_store.get('jukebox_backend')

    if _jukebox_player is None or _jukebox_backend != backend:
        with _jukebox_player_lock:
            if _jukebox_player is None or _jukebox_backend != backend:
                if _jukebox_player is not None:
                    _jukebox_player.shutdown()
                _jukebox_player = _JUKEBOX_BACKENDS.get(backend, LocalJukeboxPlayer)()
                _jukebox_backend = backend

    return _jukebox_player
