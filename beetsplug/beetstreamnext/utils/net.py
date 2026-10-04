from __future__ import annotations
import ipaddress
import re
from typing import List, NamedTuple, Optional, Set

from beetsplug.beetstreamnext.utils.text import split_list


##
# Host and URL parsing

# RFC 1123 hostname label: 1-63 alphanumerics/hyphens (not starting/ending with an hyphen)
_HOSTNAME_RE = re.compile(
    r'^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$'
)

_SCHEME = r'[a-zA-Z][a-zA-Z0-9+.-]*'
SCHEME_RE = re.compile(rf'^({_SCHEME})://')
DUPLICATE_SCHEME_RE = re.compile(rf'^(?:{_SCHEME}://)+(?={_SCHEME}://)')      # 'https://http://x' -> 'http://x'

def validate_trusted_hosts(raw: str) -> str:
    """
    Validate and normalise a comma-separated list of allowed Host header values
    (hostnames or IPs, IPv6 literals may be bracketed as in a Host header).
    """
    if not raw:
        return ''

    entries: Set[str] = set()
    invalid: List[str] = []

    for item in split_list(raw):
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
    host: str     # hostname or IP (lowercase)
    scheme: Optional[str] = None
    port: Optional[int] = None


def _parse_port(raw: str) -> int:
    if not (raw.isdecimal() and 0 < int(raw) <= 65535):
        raise ValueError(f'Invalid port: {raw}')
    return int(raw)


def parse_host(raw: str) -> ParsedHost:
    """
    Parse a host/IP (`music.example.com`, `192.168.8.184`) or a full
    `scheme://host[:port]` value into parts.

    Raises ValueError if host/IP part is invalid.
    Returns an empty ParsedHost for an empty input.
    """

    raw = (raw or '').strip()
    if not raw:
        return ParsedHost(host='')

    scheme = None
    m = SCHEME_RE.match(raw)
    if m:
        scheme = m.group(1).lower()
        raw = raw[m.end():]

    raw = raw.split('/', 1)[0]
    if not raw:
        raise ValueError('Missing host')

    if raw.startswith('['):
        # IPv6 literal optionally with a :port
        try:
            end = raw.index(']')
        except ValueError:
            raise ValueError(f'Invalid host: {raw}') from None
        host_part = raw[1:end]
        rest = raw[end + 1:]
        port = _parse_port(rest[1:]) if rest.startswith(':') else None
        ipaddress.ip_address(host_part)
        return ParsedHost(host=host_part, scheme=scheme, port=port)

    # IP (v4, or non-bracketed v6 with no port)
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
            raise ValueError(f'Invalid host: {raw}') from None
        host_part = normalized

    return ParsedHost(host=host_part, scheme=scheme, port=port)


def strip_host_port(raw_host: str) -> str:
    """Strip a `:port` from a Host header value."""
    if raw_host.startswith('['):
        try:
            return raw_host[1:raw_host.index(']')]
        except ValueError:
            raise ValueError(f'Invalid Host header: {raw_host}') from None
    return raw_host.split(':')[0]


def https_variant(url: str) -> str:
    """Returns url with its scheme flipped between http and https, or url unchanged if neither."""
    if url.lower().startswith('http://'):
        return 'https://' + url[len('http://'):]
    if url.lower().startswith('https://'):
        return 'http://' + url[len('https://'):]
    return url
