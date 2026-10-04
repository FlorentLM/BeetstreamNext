from __future__ import annotations

import itertools
import json
import os
import re
import shlex
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

from beetsplug.beetstreamnext.constants import JUKEBOX_SOCK_DIR
from beetsplug.beetstreamnext.utils.system import find_binary
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.jukebox.base import JukeboxBackend, JukeboxUnavailableException, PropertyUnavailableExceptionException


_MPV_DEVICE_RE = re.compile(r"^'([^']+)'\s*\(([^)]*)\)$")


def mpv_discovery(timeout: float = 5.0) -> List[dict]:
    """List the audio output devices mpv can see on this machine. Returns [{'name', 'device'}, ...]."""

    mpv_bin = find_binary('mpv')
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


class LocalJukeboxPlayer(JukeboxBackend):
    """
    Wrapper around a local mpv process (controlled over its json IPC socket).
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

        mpv_bin = find_binary('mpv')
        if not mpv_bin:
            raise JukeboxUnavailableException("mpv wasn't found. Install it, or update the 'mpv_path' setting.")

        from beetsplug.beetstreamnext.core.config.store import settings_store

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
