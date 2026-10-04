from __future__ import annotations

import socket
import threading
import time
import ipaddress
import urllib.parse
from collections import defaultdict
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Set, Tuple

from beetsplug.beetstreamnext.utils.net import strip_host_port
from beetsplug.beetstreamnext.utils.text import split_list
from beetsplug.beetstreamnext.core.services.events import admin_events
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.constants import (
    LOOPBACK_IPS, RATE_LIMIT_MAX_FAILURES, RATE_LIMIT_BLOCK_WINDOW,
    RATE_LIMIT_IP_MAX_FAILURES, RATE_LIMIT_IP_BLOCK_WINDOW
)

class RateLimiter:
    """
    Two-tier login rate limiter:
      1- per (IP, username) pair, resets on a successful login for that pair.
      2- per IP only, aggregating failures across *any* username tried from
        that IP. Not reset on a single username's successful login so an
        attacker spraying lots of usernames from one IP can't dodge it by rotating logins.
    """

    def __init__(
            self,
            max_failures: int = RATE_LIMIT_MAX_FAILURES,
            block_window: int = RATE_LIMIT_BLOCK_WINDOW,
            ip_max_failures: int = RATE_LIMIT_IP_MAX_FAILURES,
            ip_block_window: int = RATE_LIMIT_IP_BLOCK_WINDOW
        ):

        self._lock = threading.Lock()

        self._store: Dict[Tuple[str, str], List[float]] = defaultdict(list)
        self._ip_store: Dict[str, List[float]] = defaultdict(list)

        self._max_failures = max_failures
        self._block_window = block_window
        self._ip_max_failures = ip_max_failures
        self._ip_block_window = ip_block_window

    def is_blocked(self, ip: str, username: str = '') -> bool:
        """Check if an (IP, username) pair, or the IP itself, is currently blocked."""

        if ip in LOOPBACK_IPS:
            bsn_logger.debug(f'IP {ip} is a loopback IP, ignoring rate limiting check.')
            return False

        now = time.monotonic()
        with self._lock:
            ip_attempts = self._ip_store.get(ip)
            if ip_attempts:
                recent_ip = [t for t in ip_attempts if now - t < self._ip_block_window]
                if recent_ip:
                    self._ip_store[ip] = recent_ip
                    if len(recent_ip) >= self._ip_max_failures:
                        return True
                else:
                    self._ip_store.pop(ip, None)

            key = (ip, username)
            attempts = self._store.get(key)
            if not attempts:
                return False

            recent = [t for t in attempts if now - t < self._block_window]
            if not recent:
                self._store.pop(key, None)
                return False

            self._store[key] = recent
            exceeds = len(recent) >= self._max_failures
            return exceeds

    def record(self, ip: str, username: str = ''):
        """Log a failed attempt."""
        if ip in LOOPBACK_IPS:
            bsn_logger.debug(f'IP {ip} is a loopback IP, skipping rate limiting record.')
            return

        key = (ip, username)
        now = time.monotonic()
        with self._lock:
            self._store[key].append(now)
            self._ip_store[ip].append(now)

        admin_events.publish('rate-limits', '')

    def reset(self, ip: str, username: str = ''):
        """Clear failures for an (IP, username) pair. The IP-wide bucket is kept."""
        key = (ip, username)
        with self._lock:
            removed = self._store.pop(key, None)

        if removed:
            admin_events.publish('rate-limits', '')

    def sweep(self):
        """Remove all stale buckets from memory."""
        now = time.monotonic()
        with self._lock:
            stale = [
                key for key, attempts in self._store.items()
                if not attempts or (now - max(attempts) > self._block_window)
            ]
            for key in stale:
                self._store.pop(key, None)

            stale_ips = [
                ip for ip, attempts in self._ip_store.items()
                if not attempts or (now - max(attempts) > self._ip_block_window)
            ]
            for ip in stale_ips:
                self._ip_store.pop(ip, None)

        if stale or stale_ips:
            admin_events.publish('rate-limits', '')

    def purge(self) -> int:
        """Forget every recorded failure. Returns the number of buckets cleared."""
        with self._lock:
            n = len(self._store) + len(self._ip_store)
            self._store.clear()
            self._ip_store.clear()
        admin_events.publish('rate-limits', '')
        return n

    def report(self) -> dict:
        """Snapshot of current state for the admin panel."""
        now = time.monotonic()
        entries = []
        ip_entries = []
        with self._lock:
            for (ip, username), attempts in self._store.items():
                recent = [t for t in attempts if now - t < self._block_window]
                if not recent:
                    continue
                entries.append({
                    'ip': ip,
                    'username': username,
                    'failures': len(recent),
                    'blocked': len(recent) >= self._max_failures,
                    'oldest_failure_age_sec': round(now - min(recent), 1),
                })

            for ip, attempts in self._ip_store.items():
                recent = [t for t in attempts if now - t < self._ip_block_window]
                if not recent:
                    continue
                ip_entries.append({
                    'ip': ip,
                    'failures': len(recent),
                    'blocked': len(recent) >= self._ip_max_failures,
                    'oldest_failure_age_sec': round(now - min(recent), 1),
                })

            max_failures = self._max_failures
            block_window = self._block_window
            ip_max_failures = self._ip_max_failures
            ip_block_window = self._ip_block_window

        entries.sort(key=lambda r: (-r['failures'], r['ip'], r['username']))
        ip_entries.sort(key=lambda r: (-r['failures'], r['ip']))

        return {
            'max_failures': max_failures,
            'block_window_sec': block_window,
            'entries': entries,
            'ip_limiter': {
                'max_failures': ip_max_failures,
                'block_window_sec': ip_block_window,
                'entries': ip_entries,
            },
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

    @property
    def ip_max_failures(self) -> int:
        return self._ip_max_failures

    @ip_max_failures.setter
    def ip_max_failures(self, value: int):
        self._ip_max_failures = int(value)

    @property
    def ip_block_window(self) -> int:
        return self._ip_block_window

    @ip_block_window.setter
    def ip_block_window(self, value: int):
        self._ip_block_window = int(value)


class IPFilter:
    """
    IP allow/deny list.
    """

    def __init__(self,
                 whitelist: Optional[Sequence[str]] = None,
                 blacklist: Optional[Sequence[str]] = None
        ):

        self._whitelist_raw: Set[str] = set()
        self._blacklist_raw: Set[str] = set()
        self._whitelist_nets: Set[ipaddress._BaseNetwork] = set()
        self._blacklist_nets: Set[ipaddress._BaseNetwork] = set()

        if whitelist:
            self.whitelist = whitelist
        if blacklist:
            self.blacklist = blacklist

    @staticmethod
    def parse_ips(values: Optional[str | Sequence[str]] = None) -> Set[str]:
        """Validate a comma-separated (or sequence of) IPs/CIDR ranges, returning normalised strings."""
        raw_items = split_list(values)

        final_ips = set()
        for item in raw_items:
            try:
                if '/' in item:
                    net = ipaddress.ip_network(item, strict=False)
                    final_ips.add(str(net))
                else:
                    ip = ipaddress.ip_address(item)
                    final_ips.add(str(ip))
            except ValueError:
                bsn_logger.warning(f'Ignoring invalid IP/CIDR range: {item}')
                raise ValueError(f"'{item}' is not a valid IP address or CIDR range.")
        return final_ips

    @staticmethod
    def _to_network(item: str) -> Optional['ipaddress._BaseNetwork']:
        try:
            return ipaddress.ip_network(item, strict=False)
        except ValueError:
            return None

    def is_allowed(self, ip: str) -> bool:

        if ip in LOOPBACK_IPS:
            return True

        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            bsn_logger.warning(f'Could not parse client IP {ip!r}. Falling back to exact-match filtering.')
            addr = None

        if addr is not None:
            if any(addr in net for net in self._blacklist_nets):
                bsn_logger.info(f'IP {ip}: access denied (blacklist).')
                return False

            if self._whitelist_nets and not any(addr in net for net in self._whitelist_nets):
                bsn_logger.info(f'IP {ip}: access denied (not in whitelist).')
                return False

            return True

        # Unparseable (e.g. a placeholder like 'unknown'): fall back to exact string matching.
        if ip in self._blacklist_raw:
            bsn_logger.info(f'IP {ip}: access denied (blacklist).')
            return False

        if self._whitelist_raw and ip not in self._whitelist_raw:
            bsn_logger.info(f'IP {ip}: access denied (not in whitelist).')
            return False

        return True

    def _add(self, raw_set: Set[str], net_set: Set['ipaddress._BaseNetwork'], item: str):
        item = item.strip()
        if not item:
            return
        raw_set.add(item)
        net = self._to_network(item)
        if net is not None:
            net_set.add(net)

    def _remove(self, raw_set: Set[str], net_set: Set['ipaddress._BaseNetwork'], item: str):
        item = item.strip()
        raw_set.discard(item)
        net = self._to_network(item)
        if net is not None:
            net_set.discard(net)

    def allow(self, ip: str):
        bsn_logger.debug(f'IP {ip} added to whitelist.')
        self._add(self._whitelist_raw, self._whitelist_nets, ip)

    def disallow(self, ip: str):
        bsn_logger.debug(f'IP {ip} removed from whitelist.')
        self._remove(self._whitelist_raw, self._whitelist_nets, ip)

    def ban(self, ip: str):
        bsn_logger.debug(f'IP {ip} added to blacklist.')
        self._add(self._blacklist_raw, self._blacklist_nets, ip)

    def unban(self, ip: str):
        bsn_logger.debug(f'IP {ip} removed from blacklist.')
        self._remove(self._blacklist_raw, self._blacklist_nets, ip)

    @property
    def whitelist(self) -> Set[str]:
        return set(self._whitelist_raw)

    @whitelist.setter
    def whitelist(self, whitelisted_ips: str | Sequence[str]):
        parsed = self.parse_ips(whitelisted_ips)
        self._whitelist_raw = parsed
        self._whitelist_nets = {n for n in (self._to_network(p) for p in parsed) if n is not None}
        bsn_logger.debug(f'Loaded new whitelist: {self._whitelist_raw}.')

    @property
    def blacklist(self) -> Set[str]:
        return set(self._blacklist_raw)

    @blacklist.setter
    def blacklist(self, blacklisted_ips: str | Sequence[str]):
        parsed = self.parse_ips(blacklisted_ips)
        self._blacklist_raw = parsed
        self._blacklist_nets = {n for n in (self._to_network(p) for p in parsed) if n is not None}
        bsn_logger.debug(f'Loaded new blacklist: {self._blacklist_raw}.')


##
# Host header validation

def admin_host_allowed(raw_host: str) -> bool:
    """
    Whether `raw_host` (a request's raw Host header) is allowed to reach the admin panel,
    given the `admin_hostname` setting (unset = no restriction). Loopback is always allowed.
    """
    from beetsplug.beetstreamnext.config.store import settings_store

    admin_host = settings_store.get('admin_hostname')
    if not admin_host:
        return True
    try:
        request_host = strip_host_port(raw_host).lower()
    except ValueError:
        return False
    return request_host == admin_host or request_host in LOOPBACK_IPS


##
# SSRF guard

@lru_cache(maxsize=512)
def _hostname_is_public(hostname: str, _cache_ttl_hash=None) -> bool:
    """`_cache_ttl_hash` is just to change the function signature every x seconds to inactivate the lru."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError):
        return False

    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified
        ):
            return False

    return True


def is_public_url(url: str) -> bool:
    """
    Check if url is http(s) and every address its host resolves to is a public one
    (that is, not loopback/link-local/private/reserved/multicast/unspecified)

    Used to block SSRF before fetching a user-supplied URL.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        return False

    if parsed.scheme not in ('http', 'https') or not hostname:
        return False

    return _hostname_is_public(hostname, _cache_ttl_hash=round(time.time() / 300))


##
# Instanciate shared objects

ip_filter = IPFilter()

rate_limiter = RateLimiter(
    max_failures=RATE_LIMIT_MAX_FAILURES, block_window=RATE_LIMIT_BLOCK_WINDOW,
    ip_max_failures=RATE_LIMIT_IP_MAX_FAILURES, ip_block_window=RATE_LIMIT_IP_BLOCK_WINDOW
)