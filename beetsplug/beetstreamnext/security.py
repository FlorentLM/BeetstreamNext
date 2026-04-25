import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Set

from beetsplug.beetstreamnext.constants import LOOPBACK_IPS, bsn_logger


class RateLimiter:

    def __init__(self, max_failures: int = 5, block_window: int = 300):

        self._lock = threading.Lock()

        self._store: Dict[str, List[float]] = defaultdict(list)

        self._max_failures = max_failures
        self._block_window = block_window

    def is_blocked(self, ip: str) -> bool:
        """Check if an IP is currently blocked."""

        if ip in LOOPBACK_IPS:
            bsn_logger.debug(f'IP {ip} is in the loopback IPs list, ignoring rate limiting check.')
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
            bsn_logger.debug(f'IP {ip} is in the loopback IPs list, skipping rate limiting record.')
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

    @staticmethod
    def _parse_input(values: Optional[str | Sequence[str]] = None) -> Set[str]:
        """Belt and suspenders parser to grab config values."""
        if not values:
            values = []
        if isinstance(values, str):
            values = [v.strip() for v in values.split(',')]
        else:
            values = [vv.strip(',') for v in values for vv in v.split(',')]
        return {v for v in values if v}

    def is_allowed(self, ip: str) -> bool:

        if ip in LOOPBACK_IPS:
            return True

        if ip in self._blacklist:
            bsn_logger.info(f'IP {ip}: access denied (blacklist).')
            return False

        if self._whitelist and ip not in self._whitelist:
            bsn_logger.info(f'IP {ip}: access denied (not in whitelist).')
            return False

        return True

    def allow(self, ip: str):
        bsn_logger.debug(f'IP {ip} added to whitelist.')
        self._whitelist.add(ip)

    def disallow(self, ip: str):
        bsn_logger.debug(f'IP {ip} removed from whitelist.')
        self._whitelist.discard(ip)

    def ban(self, ip: str):
        bsn_logger.debug(f'IP {ip} added to blacklist.')
        self._blacklist.add(ip)

    def unban(self, ip: str):
        bsn_logger.debug(f'IP {ip} removed from blacklist.')
        self._blacklist.discard(ip)

    @property
    def whitelist(self) -> Set[str]:
        return self._whitelist

    @whitelist.setter
    def whitelist(self, whitelisted_ips: str | Sequence[str]):
        self._whitelist = self._parse_input(whitelisted_ips)
        bsn_logger.debug(f'Loaded new whitelist: {self._whitelist}.')

    @property
    def blacklist(self) -> Set[str]:
        return self._blacklist

    @blacklist.setter
    def blacklist(self, blacklisted_ips: str | Sequence[str]):
        self._blacklist = self._parse_input(blacklisted_ips)
        bsn_logger.debug(f'Loaded new blacklist: {self._blacklist}.')
