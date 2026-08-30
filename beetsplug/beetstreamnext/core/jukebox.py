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
from typing import List, Optional, Tuple

from beetsplug.beetstreamnext.constants import JUKEBOX_SOCK_DIR, SOCO
from beetsplug.beetstreamnext.public.tokeniser import stream_tokeniser
from beetsplug.beetstreamnext.utils.system import find_mpv
from beetsplug.beetstreamnext.utils.text import parse_duration, format_duration
from beetsplug.beetstreamnext.utils.general import external_url
from beetsplug.beetstreamnext.core.logging import bsn_logger



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
    NAME = 'mpv'

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
                bsn_logger.warning(f'Jukebox: [mpv] {line}')

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


_URL_EXT_RE = re.compile(r'\.(mp3|aac|ogg|oga|flac|wav|m4a|opus|mp4|m3u8?)(?:$|\?)', re.IGNORECASE)


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

        ip = settings_store.get('jukebox_sonos_ip')
        if not ip:
            raise JukeboxUnavailableException('No Sonos speaker selected. Pick one in the admin panel.')

        if self._device is None or self._device_ip != ip:
            import soco
            self._device = soco.SoCo(ip)
            self._device_ip = ip

    def _resolve_uri(self, path: str) -> str:
        """
        Local files, and http(s) URLs without a recognisable audio extension
        are exposed to the speaker as tokenised stream URLs.
        URLs that already end with a known extension can be passed straight through.
        """

        is_url = path.startswith(('http://', 'https://'))
        if is_url and _URL_EXT_RE.search(path):
            return path

        import flask

        token = stream_tokeniser.register(path)

        # Sonos needs an extension in the URL, otherwise it rejects it (UPnP error 804)
        filename = (Path(path).name if not is_url else '') or 'stream.mp3'
        path_part = flask.url_for('public.tokenised_stream', token=token, filename=filename)

        return external_url(path_part)

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


##
# Lazy instantiation (but with hot-swap)

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
                _jukebox_player = SonosJukeboxPlayer() if backend == 'sonos' else LocalJukeboxPlayer()
                _jukebox_backend = backend

    return _jukebox_player
