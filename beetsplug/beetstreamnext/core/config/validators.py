from __future__ import annotations

import ipaddress
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, List
from uuid import UUID

from beetsplug.beetstreamnext.constants import UNIX_SOCKET_PREFIX
from beetsplug.beetstreamnext.utils.net import parse_host



def validate_int_range(lo: int, hi: int) -> Callable[[Any], int]:
    def _v(x: Any) -> int:
        n = int(x)
        if not lo <= n <= hi:
            raise ValueError(f'Must be between {lo} and {hi}')
        return n
    return _v


def validate_choice(*options: str) -> Callable[[Any], str]:
    def _v(x: Any) -> str:
        s = str(x)
        if s not in options:
            raise ValueError(f"Must be one of: {', '.join(options)}")
        return s
    return _v


def validate_path(x: Any) -> str:
    s = str(x or '').strip()
    if s and shutil.which(s) is None:
        raise ValueError('Not an executable file (or not found).')
    return s


def validate_storage_dir(x: Any) -> str:
    """
    Directory where BSN stores stuff: absolute, not the filesystem root, and not overlapping with the music library.
    """

    s = str(x or '').strip()
    if not s:
        return s

    path = Path(s).expanduser()
    if not path.is_absolute():
        raise ValueError('Use an absolute path.')

    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("Can't be the filesystem root.")

    from beetsplug.beetstreamnext.application import app
    music_root = app.config.get('music_root')
    if music_root:
        music_root = Path(music_root).resolve()
        if resolved.is_relative_to(music_root) or music_root.is_relative_to(resolved):
            raise ValueError('Must not be inside the music library, or contain it.')

    return s


def validate_external_hostname(x: Any) -> str:
    """Hostname/IP, keeping port if given. Any scheme prefix is stripped."""
    parsed = parse_host(str(x or ''))
    if not parsed.host:
        return ''
    return f'{parsed.host}:{parsed.port}' if parsed.port else parsed.host


def validate_jukebox_device(x: Any) -> str:
    """
    'jukebox_hardware_device' can be a Sonos IP, or a Chromecast UUID/IP/hostname
    (or a an mpv audio-device string but that's not checked here)
    """
    s = str(x or '').strip()
    if not s:
        return s

    from beetsplug.beetstreamnext.core.config.store import settings_store
    backend = settings_store.get('jukebox_backend')

    if backend == 'sonos':
        try:
            ipaddress.ip_address(s)
        except ValueError:
            raise ValueError("Must be the Sonos speaker's IP address.") from None
    elif backend == 'chromecast':
        try:
            UUID(s)
        except ValueError:
            parsed = parse_host(s)
            if not parsed.host or parsed.scheme or parsed.port:
                raise ValueError("Must be the Chromecast's UUID, IP address, or hostname.") from None

    return s


def validate_host_list(hosts: List[str]) -> List[str]:
    """
    Each entry must be a bare hostname/IP to bind to (no scheme or port),
    or a single `unix:/absolute/path` socket.
    """

    if any(h.startswith(UNIX_SOCKET_PREFIX) for h in hosts):

        if len(hosts) > 1:
            raise ValueError('A unix socket cannot be combined with other hosts.')

        path = hosts[0][len(UNIX_SOCKET_PREFIX):]

        if sys.platform == 'win32':
            raise ValueError('Unix sockets are not available on Windows.')
        if not path.startswith('/') or path.endswith('/'):
            raise ValueError(f"'{hosts[0]}' must be 'unix:' followed by the absolute path of the socket file.")
        if len(path.encode()) > 100:        # sockaddr_un.sun_path is 104 bytes on macOS, 108 on Linux
            raise ValueError('That socket path is too long.')

        return [hosts[0]]

    result = []
    for h in hosts:
        parsed = parse_host(h)
        if not parsed.host or parsed.scheme or parsed.port:
            raise ValueError(f"'{h}' is not a bare hostname or IP address.")
        result.append(parsed.host)
    return result


def validate_host_list_nosocket(hosts: List[str]) -> List[str]:
    """validate_host_list validator but excluding unix sockets."""

    if any(h.startswith(UNIX_SOCKET_PREFIX) for h in hosts):
        raise ValueError('A unix socket cannot be public-only.')

    return validate_host_list(hosts)
