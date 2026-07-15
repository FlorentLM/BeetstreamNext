import threading
import time
import ipaddress
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Set

from beetsplug.beetstreamnext.constants import (
    LOOPBACK_IPS, RATE_LIMIT_MAX_FAILURES, RATE_LIMIT_BLOCK_WINDOW, bsn_logger
)

# TODO: Support CIDR


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

    def purge(self) -> int:
        """Forget every recorded failure. Returns the number of IPs cleared."""
        with self._lock:
            n = len(self._store)
            self._store.clear()
        return n

    def report(self) -> Dict:
        """Snapshot of the current state  for diagnostics."""
        now = time.monotonic()
        entries = []
        with self._lock:
            for ip, attempts in self._store.items():
                recent = [t for t in attempts if now - t < self._block_window]
                if not recent:
                    continue
                entries.append({
                    'ip': ip,
                    'failures': len(recent),
                    'blocked': len(recent) >= self._max_failures,
                    'oldest_failure_age_sec': round(now - min(recent), 1),
                })
            max_failures = self._max_failures
            block_window = self._block_window

        entries.sort(key=lambda r: (-r['failures'], r['ip']))
        return {
            'max_failures': max_failures,
            'block_window_sec': block_window,
            'entries': entries,
        }

    # Tunable at runtime by the settings store
    @property
    def max_failures(self) -> int:
        return self._max_failures

    @max_failures.setter
    def max_failures(self, value: int):
        self._max_failures = int(value)

    @property
    def block_window(self) -> int:
        return self._block_window

    @block_window.setter
    def block_window(self, value: int):
        self._block_window = int(value)


class IPFilter:
    def __init__(self,
                 whitelist: Optional[Sequence[str]] = None,
                 blacklist: Optional[Sequence[str]] = None
        ):

        self._whitelist = set(whitelist) if whitelist else set()
        self._blacklist = set(blacklist) if blacklist else set()

    @staticmethod
    def _parse_input(values: Optional[str | Sequence[str]] = None) -> Set[str]:
        if not values:
            return set()

        if isinstance(values, str):
            raw_items = [v.strip() for v in values.split(',')]
        else:
            raw_items = [vv.strip(',') for v in values for vv in v.split(',')]

        final_ips = set()
        for item in raw_items:
            if not item: continue
            try:
                ipaddress.ip_address(item)
                final_ips.add(item)
            except ValueError:
                bsn_logger.warning(f'Ignoring invalid IP address: {item}')
                raise ValueError(f"'{item}' is not a valid IP.")
        return final_ips

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


##
# Instanciate shared objects

ip_filter = IPFilter()

rate_limiter = RateLimiter(max_failures=RATE_LIMIT_MAX_FAILURES, block_window=RATE_LIMIT_BLOCK_WINDOW)