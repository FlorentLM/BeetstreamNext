import re
import threading
import time
import ipaddress
from collections import defaultdict
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

from beetsplug.beetstreamnext.core.logging import bsn_logger
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
            max_failures: int = 5,
            block_window: int = 300,
            ip_max_failures: int = 20,
            ip_block_window: int = 3600
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

    def reset(self, ip: str, username: str = ''):
        """Clear failures for an (IP, username) pair. The IP-wide bucket is kept."""
        key = (ip, username)
        with self._lock:
            self._store.pop(key, None)

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

    def purge(self) -> int:
        """Forget every recorded failure. Returns the number of buckets cleared."""
        with self._lock:
            n = len(self._store) + len(self._ip_store)
            self._store.clear()
            self._ip_store.clear()
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
        if not values:
            return set()

        if isinstance(values, str):
            raw_items = [v.strip() for v in values.split(',')]
        else:
            raw_items = [vv.strip() for v in values for vv in v.split(',')]

        final_ips = set()
        for item in raw_items:
            if not item:
                continue
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

# RFC 1123 hostname label: 1-63 alphanumerics/hyphens (not starting/ending with an hyphen)
_HOSTNAME_RE = re.compile(
    r'^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$'
)

_SCHEME_RE = re.compile(r'^([a-zA-Z][a-zA-Z0-9+.-]*)://')
_PORT_RE = re.compile(r'^\d{1,5}$')

def validate_trusted_hosts(raw: str) -> str:
    """
    Validate and normalise a comma-separated list of allowed Host header values
    (hostnames or bare IPs, IPv6 literals may be bracketed as in a Host header).
    """
    if not raw:
        return ''

    entries: Set[str] = set()
    invalid: List[str] = []

    for item in raw.split(','):
        item = item.strip()
        if not item:
            continue

        candidate = item[1:-1] if item.startswith('[') and item.endswith(']') else item

        try:
            ipaddress.ip_address(candidate)
            entries.add(candidate)
            continue
        except ValueError:
            pass

        normalized = candidate.rstrip('.').lower()
        if bool(_HOSTNAME_RE.match(normalized)):
            entries.add(normalized)
        else:
            invalid.append(item)

    if invalid:
        raise ValueError(f"Invalid host(s) in trusted_hosts: {', '.join(invalid)}")

    return ','.join(sorted(entries))


class ParsedHost(NamedTuple):
    host: str                    # bare hostname or IP (lowercase)
    scheme: Optional[str] = None
    port: Optional[int] = None


def _parse_port(raw: str) -> int:
    if not _PORT_RE.match(raw) or not (0 < int(raw) <= 65535):
        raise ValueError(f'Invalid port: {raw}')
    return int(raw)


def parse_host(raw: str) -> ParsedHost:
    """
    Parse a bare host/IP (`music.example.com`, `192.168.8.184`) or a full
    `scheme://host[:port]` value into its parts.

    Raises ValueError if host/IP part is invalid.
    Returns an empty ParsedHost for an empty input.
    """
    raw = (raw or '').strip()
    if not raw:
        return ParsedHost(host='')

    scheme = None
    m = _SCHEME_RE.match(raw)
    if m:
        scheme = m.group(1).lower()
        raw = raw[m.end():]

    raw = raw.split('/', 1)[0]  # drop any trailing path/query
    if not raw:
        raise ValueError('Missing host')

    if raw.startswith('['):
        # bracketed IPv6 literal, optionally followed by :port
        try:
            end = raw.index(']')
        except ValueError:
            raise ValueError(f'Invalid host: {raw}')
        host_part = raw[1:end]
        rest = raw[end + 1:]
        port = _parse_port(rest[1:]) if rest.startswith(':') else None
        ipaddress.ip_address(host_part)
        return ParsedHost(host=host_part, scheme=scheme, port=port)

    # bare IP (v4, or unbracketed v6 with no port)
    try:
        ipaddress.ip_address(raw)
        return ParsedHost(host=raw, scheme=scheme, port=None)
    except ValueError:
        pass

    head, sep, tail = raw.rpartition(':')
    host_part, port = (head, _parse_port(tail)) if sep else (tail, None)

    try:
        ipaddress.ip_address(host_part)
    except ValueError:
        normalized = host_part.rstrip('.').lower()
        if not _HOSTNAME_RE.match(normalized):
            raise ValueError(f'Invalid host: {raw}')
        host_part = normalized

    return ParsedHost(host=host_part, scheme=scheme, port=port)


def strip_host_port(raw_host: str) -> str:
    """Strip an optional `:port` from a Host header value, handling bracketed IPv6 literals."""
    if raw_host.startswith('['):
        try:
            return raw_host[1:raw_host.index(']')]
        except ValueError:
            raise ValueError(f'Invalid Host header: {raw_host}')
    return raw_host.split(':')[0]


##
# Instanciate shared objects

ip_filter = IPFilter()

rate_limiter = RateLimiter(
    max_failures=RATE_LIMIT_MAX_FAILURES, block_window=RATE_LIMIT_BLOCK_WINDOW,
    ip_max_failures=RATE_LIMIT_IP_MAX_FAILURES, ip_block_window=RATE_LIMIT_IP_BLOCK_WINDOW
)