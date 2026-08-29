import itertools
import json
import os
import random
import shlex
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

from beetsplug.beetstreamnext.constants import JUKEBOX_SOCK_DIR, find_mpv
from beetsplug.beetstreamnext.core.logging import bsn_logger



class JukeboxUnavailable(Exception):
    """Raised when the jukebox backend can't be started or reached."""


class _PropertyUnavailable(JukeboxUnavailable):
    """Internal: an mpv property is momentarily unavailable."""


class JukeboxPlayer:
    """
    Wrapper around a local mpv process controlled over its json IPC socket.
    """

    def __init__(self):

        self._lock = threading.RLock()

        self._proc: Optional[subprocess.Popen] = None

        self._sock: Optional[socket.socket] = None
        self._sock_file = None
        self._sock_path: Optional[Path] = None

        self._req_ids = itertools.count(1)
        self._queue: List[Tuple[str, str]] = []   # (song_id, absolute_path) indexed on mpv's playlist

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and self._sock is not None

    def _ensure_started(self):
        if self._alive():
            return

        if self._proc is not None:
            bsn_logger.warning(
                f'Jukebox: mpv is not running (last exit code: {self._proc.poll()}). (re)starting it.'
            )

        mpv_bin = find_mpv()
        if not mpv_bin:
            raise JukeboxUnavailable("mpv wasn't found. Install it, or update the 'mpv_path' setting.")

        from beetsplug.beetstreamnext.settings import settings_store

        JUKEBOX_SOCK_DIR.mkdir(parents=True, exist_ok=True)
        sock_path = JUKEBOX_SOCK_DIR / f'mpv-{os.getpid()}.sock'
        if sock_path.exists():
            sock_path.unlink()

        args = [
            mpv_bin, '--no-video', '--idle=yes', '--msg-level=all=warn',
            f'--input-ipc-server={sock_path}',
        ]
        audio_device = settings_store.get('jukebox_audio_device')
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
            raise JukeboxUnavailable("mpv didn't open its IPC socket in time.")

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

        self._ensure_started()

        req_id = next(self._req_ids)

        try:
            self._sock_file.write((json.dumps({'command': list(args), 'request_id': req_id}) + '\n').encode('utf-8'))
            self._sock_file.flush()

            while True:
                line = self._sock_file.readline()
                if not line:
                    raise JukeboxUnavailable('mpv closed its IPC connection.')

                msg = json.loads(line)
                if msg.get('request_id') == req_id:
                    err = msg.get('error')

                    if err != 'success':
                        if err == 'property unavailable':
                            raise _PropertyUnavailable(f"mpv property unavailable: {args!r}")
                        raise JukeboxUnavailable(f"mpv command {args!r} failed: {err}")

                    return msg.get('data')

                # else: discard

        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            self._terminate()
            raise JukeboxUnavailable(f'Lost connection to mpv: {e}') from e

    def _get(self, prop, default=None):
        try:
            return self._command('get_property', prop)
        except JukeboxUnavailable:
            return default

    def _clear_mpv_playlist(self):
        self._command('stop')
        self._command('playlist-clear')

    def _loadfile(self, path: str, mode: str):
        bsn_logger.info(f'Jukebox: loadfile {mode} -> {path}')
        self._command('loadfile', path, mode)

    def status(self) -> dict:

        with self._lock:

            if not self._alive() and not self._queue:
                jukeboxStatus = {
                    'currentIndex': -1,
                    'playing': False,
                    'gain': 1.0,
                    'position': 0
                }

            playlist_pos = self._get('playlist-pos', -1)
            if playlist_pos is None:
                playlist_pos = -1

            paused = bool(self._get('pause', True))
            volume = self._get('volume', 100.0) or 100.0
            position = self._get('time-pos', 0) or 0

            jukeboxStatus = {
                'currentIndex': playlist_pos,
                'playing': not paused and playlist_pos >= 0,
                'gain': round(volume / 100.0, 4),
                'position': int(position),
            }
            return jukeboxStatus

    def track_ids(self) -> List[str]:
        with self._lock:
            return [sid for sid, _ in self._queue]

    def set_playlist(self, entries: List[Tuple[str, str]]):
        """Replace the playlist and start playing from the first track."""

        with self._lock:
            self._ensure_started()
            self._clear_mpv_playlist()

            for i, (_, path) in enumerate(entries):
                self._loadfile(path, 'append' if i else 'replace')
            self._queue = list(entries)

            if entries:
                self._command('set_property', 'pause', False)

    def add(self, entries: List[Tuple[str, str]]):
        with self._lock:
            self._ensure_started()
            for _, path in entries:
                self._loadfile(path, 'append')
            self._queue.extend(entries)

    def clear(self):
        with self._lock:
            if self._alive():
                self._clear_mpv_playlist()
            self._queue = []

    def remove(self, index: int):
        with self._lock:
            if not (0 <= index < len(self._queue)):
                return
            self._command('playlist-remove', index)
            del self._queue[index]

    def shuffle(self):

        with self._lock:
            if not self._queue:
                return
            was_playing = not bool(self._get('pause', True))
            random.shuffle(self._queue)
            self._clear_mpv_playlist()

            for i, (_, path) in enumerate(self._queue):
                self._loadfile(path, 'append' if i else 'replace')
            self._command('set_property', 'pause', not was_playing)

    def start(self):

        with self._lock:
            if not self._queue:
                return
            self._ensure_started()
            self._command('set_property', 'pause', False)

    def stop(self):

        with self._lock:
            if self._alive():
                self._command('set_property', 'pause', True)

    def skip(self, index: int, offset: float = 0.0):

        with self._lock:
            if not (0 <= index < len(self._queue)):
                raise ValueError('index out of range')

            self._ensure_started()
            bsn_logger.info(f'Jukebox: skip -> index {index} (offset {offset}s)')

            self._command('set_property', 'playlist-pos', index)
            self._command('set_property', 'pause', False)

            if offset:
                # 'time-pos' is sometimes briefly unavailable while mpv is loading the next track
                for _ in range(20):
                    try:
                        self._command('set_property', 'time-pos', offset)
                        break
                    except _PropertyUnavailable:
                        time.sleep(0.05)
                else:
                    bsn_logger.warning(f'Jukebox: could not seek to offset {offset}s after skip.')

    def set_gain(self, gain: float):

        with self._lock:
            self._ensure_started()
            self._command('set_property', 'volume', max(0.0, min(1.0, gain)) * 100.0)


##
# Lazy instantiation

_jukebox_player: Optional[JukeboxPlayer] = None
_jukebox_player_lock = threading.Lock()


def get_jukebox_player() -> JukeboxPlayer:
    global _jukebox_player

    if _jukebox_player is None:
        with _jukebox_player_lock:
            if _jukebox_player is None:
                _jukebox_player = JukeboxPlayer()

    return _jukebox_player
