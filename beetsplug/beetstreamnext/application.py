import os
import platform
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from flask import Flask
from flask_wtf.csrf import CSRFProtect

from .constants import PROJECT_ROOT, LOOPBACK_IPS, LOG_LEVEL
from .db import close_database


def cache_location() -> Path:
    if platform.system() == "Windows":
        cache_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif platform.system() == "Darwin":
        cache_dir = Path.home() / "Library" / "Caches"
    else:
        cache_dir = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

    final_path = cache_dir / "beetstreamnext"
    final_path.mkdir(parents=True, exist_ok=True)
    return final_path


class RateLimiter:

    def __init__(self, max_failures: int = 5, block_window: int = 300):

        self._lock = threading.Lock()

        self._store: Dict[str, List[float]] = defaultdict(list)

        self._max_failures = max_failures
        self._block_window = block_window

    def is_blocked(self, ip: str) -> bool:
        """Check if an IP is currently blocked."""

        if ip in LOOPBACK_IPS:
            app.logger.debug(f'IP {ip} is in the loopback IPs list, ignoring rate limiting check.')
            return False

        now = time.monotonic()
        with self._lock:
            attempts = self._store.get(ip)
            if not attempts:
                return False

            recent = [t for t in attempts if now - t < self._block_window]
            if not recent:
                self._store.pop(ip, None)
                return False

            self._store[ip] = recent
            exceeds = len(recent) >= self._max_failures
            return exceeds

    def record(self, ip: str):
        """Log a failed attempt for an IP."""
        if ip in LOOPBACK_IPS:
            app.logger.debug(f'IP {ip} is in the loopback IPs list, skipping rate limiting record.')
            return

        now = time.monotonic()
        with self._lock:
            self._store[ip].append(now)

    def reset(self, ip: str):
        """Clear failures for an IP."""
        with self._lock:
            self._store.pop(ip, None)

    def sweep(self):
        """Remove all stale IPs from memory."""
        now = time.monotonic()
        with self._lock:
            stale_ips = [
                ip for ip, attempts in self._store.items()
                if not attempts or (now - max(attempts) > self._block_window)
            ]
            for ip in stale_ips:
                self._store.pop(ip, None)


class IPFilter:
    def __init__(self,
                 whitelist: Optional[Sequence[str]] = None,
                 blacklist: Optional[Sequence[str]] = None
        ):

        self._whitelist = set(whitelist) if whitelist else set()
        self._blacklist = set(blacklist) if blacklist else set()

    def is_allowed(self, ip: str) -> bool:

        if ip in LOOPBACK_IPS:
            return True

        if ip in self._blacklist:
            app.logger.info(f'IP {ip}: access denied (blacklist).')
            return False

        if self._whitelist and ip not in self._whitelist:
            app.logger.info(f'IP {ip}: access denied (not in whitelist).')
            return False

        return True

    def allow(self, ip: str):
        app.logger.debug(f'IP {ip} added to whitelist.')
        self._whitelist.add(ip)

    def disallow(self, ip: str):
        app.logger.debug(f'IP {ip} removed from whitelist.')
        self._whitelist.discard(ip)

    def ban(self, ip: str):
        app.logger.debug(f'IP {ip} added to blacklist.')
        self._blacklist.add(ip)

    def unban(self, ip: str):
        app.logger.debug(f'IP {ip} removed from blacklist.')
        self._blacklist.discard(ip)

    @property
    def whitelist(self) -> Set[str]:
        return self._whitelist

    @whitelist.setter
    def whitelist(self, whitelisted_ips: str | Sequence[str]):
        if isinstance(whitelisted_ips, str):
            whitelisted_ips = [whitelisted_ips] if whitelisted_ips else []
        self._whitelist = set(whitelisted_ips) if whitelisted_ips else set()
        app.logger.debug(f'Loaded new whitelist: {self._whitelist}.')

    @property
    def blacklist(self) -> Set[str]:
        return self._blacklist

    @blacklist.setter
    def blacklist(self, blacklisted_ips: str | Sequence[str]):
        if isinstance(blacklisted_ips, str):
            blacklisted_ips = [blacklisted_ips] if blacklisted_ips else []
        self._blacklist = set(blacklisted_ips) if blacklisted_ips else set()
        app.logger.debug(f'Loaded new blacklist: {self._blacklist}.')


##

app = Flask(
    __name__,
    template_folder='templates',
    static_folder='static',
)
app.teardown_appcontext(close_database)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=3600,   # 1 hour
    # SESSION_COOKIE_SECURE=True,   # TODO: Have this automatically on if https or reverse proxy is detected
    WTF_CSRF_CHECK_DEFAULT=False,
    PROJECT_ROOT=PROJECT_ROOT,
    IMAGES_PATH=PROJECT_ROOT / 'static' / 'images',
    HTTP_CACHE_PATH=cache_location() / 'httpcache.sqlite',
    THUMBNAIL_CACHE_PATH=cache_location() / 'thumbnails',
)
app.config['THUMBNAIL_CACHE_PATH'].mkdir(parents=True, exist_ok=True)
# TODO: Add 'TRUSTED_HOSTS'

app.logger.setLevel(LOG_LEVEL)
app.logger.propagate = True

csrf = CSRFProtect(app)
ip_filter = IPFilter()
rate_limiter = RateLimiter(max_failures=5, block_window=300)