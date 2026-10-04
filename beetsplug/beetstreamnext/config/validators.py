from __future__ import annotations

import ipaddress
import shutil
from typing import Any, Callable, List
from uuid import UUID

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


def validate_admin_hostname(x: Any) -> str:
    """Bare lowercase hostname/IP. admin_hostname is matched against a port-stripped Host header."""
    return parse_host(str(x or '')).host


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

    from beetsplug.beetstreamnext.config.store import settings_store
    backend = settings_store.get('jukebox_backend')

    if backend == 'sonos':
        try:
            ipaddress.ip_address(s)
        except ValueError:
            raise ValueError("Must be the Sonos speaker's IP address.")
    elif backend == 'chromecast':
        try:
            UUID(s)
        except ValueError:
            parsed = parse_host(s)
            if not parsed.host or parsed.scheme or parsed.port:
                raise ValueError("Must be the Chromecast's UUID, IP address, or hostname.")

    return s


def validate_host_list(hosts: List[str]) -> List[str]:
    """Each entry must be a bare hostname/IP to bind to, no scheme or port."""
    result = []
    for h in hosts:
        parsed = parse_host(h)
        if not parsed.host or parsed.scheme or parsed.port:
            raise ValueError(f"'{h}' is not a bare hostname or IP address.")
        result.append(parsed.host)
    return result
