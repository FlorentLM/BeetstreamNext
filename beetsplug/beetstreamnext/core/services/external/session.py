from __future__ import annotations

import functools
import ipaddress
import socket
import time
import threading
from datetime import timedelta
import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import NameResolutionError, NewConnectionError
from urllib3.util.connection import create_connection
from requests_cache import CachedSession

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import MAX_REMOTE_IMAGE_BYTES, USER_AGENT
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.utils.net import (
    is_public_ip, is_public_url, https_variant, build_local_services, NO_LOCAL_SERVICES, LocalServices,
    SCHEME_RE, DUPLICATE_SCHEME_RE
)


class RequestThrottle(HTTPAdapter):
    """
    HTTPAdapter enforcing a minimum interval between requests.
    Cache hits are unaffected, this paces only real network calls.
    """

    def __init__(self, min_interval: float, *args, **kwargs):
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._next_ok = 0.0
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        with self._lock:
            wait = self._next_ok - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next_ok = time.monotonic() + self._min_interval
        return super().send(request, **kwargs)


##
# Public-only connections

def _local_services() -> LocalServices:
    """The admin's whitelist of trusted services on the local network."""
    try:
        return build_local_services(settings_store.get('local_services') or [])
    except ValueError:
        return NO_LOCAL_SERVICES


def _public_only_connect(
    host: str, port: int, timeout, source_address, socket_options, local_services: LocalServices = NO_LOCAL_SERVICES
) -> socket.socket:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except UnicodeError as e:
        raise socket.gaierror(str(e)) from e

    last_error: Exception | None = None
    for _family, _, _, _, sockaddr in infos:
        ip = sockaddr[0].split('%', 1)[0]

        addr = ipaddress.ip_address(ip)
        if not (is_public_ip(addr) or local_services.allows(host, addr)):
            last_error = OSError(f'Refusing to connect to non-public address {ip} ({host})')
            continue
        try:
            return create_connection((ip, port), timeout, source_address=source_address, socket_options=socket_options)
        except OSError as e:
            last_error = e

    raise last_error or OSError(f'No address found for {host}')


class _PublicOnlyMixin:
    """Connection mixin: only opens sockets to public addresses (and local services if 'allow_local_services')."""

    allow_local_services: bool = False

    def _new_conn(self) -> socket.socket:
        try:
            return _public_only_connect(
                self._dns_host, self.port, self.timeout, self.source_address, self.socket_options,
                _local_services() if self.allow_local_services else NO_LOCAL_SERVICES
            )
        except socket.gaierror as e:
            raise NameResolutionError(self.host, self, e) from e
        except OSError as e:
            raise NewConnectionError(self, f'Failed to establish a new connection: {e}') from e


class _PublicOnlyHTTPConnection(_PublicOnlyMixin, HTTPConnection):
    """http connection restricted to public addresses."""


class _PublicOnlyHTTPSConnection(_PublicOnlyMixin, HTTPSConnection):
    """https connection restricted to public addresses."""


class _PublicOnlyHTTPPool(HTTPConnectionPool):
    """http pool using public-only connections."""
    ConnectionCls = _PublicOnlyHTTPConnection


class _PublicOnlyHTTPSPool(HTTPSConnectionPool):
    """https pool using public-only connections."""
    ConnectionCls = _PublicOnlyHTTPSConnection


class _LocalServicesHTTPConnection(_PublicOnlyHTTPConnection):
    allow_local_services = True


class _LocalServicesHTTPSConnection(_PublicOnlyHTTPSConnection):
    allow_local_services = True


class _LocalServicesHTTPPool(_PublicOnlyHTTPPool):
    ConnectionCls = _LocalServicesHTTPConnection


class _LocalServicesHTTPSPool(_PublicOnlyHTTPSPool):
    ConnectionCls = _LocalServicesHTTPSConnection


class PublicOnlyAdapter(HTTPAdapter):
    """
    HTTPAdapter that refuses to connect to private/loopback/link-local/reserved addresses.
    (connections made through a https(s) proxy aren't affected)
    """

    def __init__(self, *args, allow_local_services: bool = False, **kwargs):
        self._allow_local_services = allow_local_services
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)

        # Addresses and hostnames whitelisted in 'local_services' setting are also accepted
        if self._allow_local_services:
            self.poolmanager.pool_classes_by_scheme = {'http': _LocalServicesHTTPPool, 'https': _LocalServicesHTTPSPool}
        else:
            self.poolmanager.pool_classes_by_scheme = {'http': _PublicOnlyHTTPPool, 'https': _PublicOnlyHTTPSPool}


def _build_session(allow_local_services: bool) -> CachedSession:
    session = CachedSession(
        str(app.config['HTTP_CACHE_PATH']),
        backend='sqlite',
        expire_after=timedelta(days=30),
        allowable_codes=[200],
        stale_if_error=True     # serve expired cached version if remote server goes down
    )

    session.headers.update({'User-Agent': USER_AGENT})

    adapter = PublicOnlyAdapter(allow_local_services=allow_local_services)
    session.mount('http://', adapter)
    session.mount('https://', adapter)

    # MusicBrainz's courtesy limit is ~50 req/s, we throttle at 25 req/s
    # https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
    musicbrainz_adapter = RequestThrottle(min_interval=0.04)
    session.mount('https://musicbrainz.org', musicbrainz_adapter)
    session.mount('http://musicbrainz.org', musicbrainz_adapter)
    return session


@functools.cache
def http_session() -> CachedSession:
    """Public internet only."""
    return _build_session(allow_local_services=False)


@functools.cache
def service_session() -> CachedSession:
    """Public internet + the services whitelisted in 'local_services'."""
    return _build_session(allow_local_services=True)


def fetch_public(url: str, *, cache: bool = False, **kwargs) -> requests.Response:
    """
    Streamed GET of a user-passed URL (feed, episode, radio stream, etc)
    ValueError if URL isn't public, any redirect to a non-public host fails at connection time
    """
    if not is_public_url(url):
        raise ValueError(f'Refusing to fetch non-public URL: {url}')

    kwargs.setdefault('headers', {'User-Agent': USER_AGENT})
    if cache:
        return http_session().get(url, stream=True, **kwargs)

    with http_session().cache_disabled():
        return http_session().get(url, stream=True, **kwargs)


def normalize_url(url: str, probe_https: bool = False, probe_timeout: float = 3.0) -> str:
    """
    Cleans up a client-supplied URL

    Args:
        - probe_https: if True and the URL is http, a HEAD request is sent at the https
        equivalent and the URL is upgraded if the server responds
        - probe_timeout: timeout in seconds for the probe

    Falls back silently to http on any error.
    """
    url = url.strip()

    if not url:
        return url

    url = DUPLICATE_SCHEME_RE.sub('', url)

    if not SCHEME_RE.match(url):
        url = f'https://{url}'

    if not url.lower().startswith(('http://', 'https://')):
        raise ValueError('Only http:// and https:// URLs are supported.')

    if probe_https and url.lower().startswith('http://'):
        https_url = https_variant(url)

        if not is_public_url(https_url):
            return url

        try:
            with http_session().cache_disabled():
                resp = http_session().head(
                    https_url, timeout=probe_timeout, stream=True, allow_redirects=True,
                    headers={'User-Agent': USER_AGENT}
                )
            resp.close()
            url = https_url
        except Exception:
            pass

    return url


def capped_image_fetch(url: str, *, max_bytes: int = MAX_REMOTE_IMAGE_BYTES, **kwargs) -> bytes:
    """GET image bytes, refusing bodies over max_bytes. Returns b'' on failure."""

    if not is_public_url(url):
        bsn_logger.warning(f'Refusing to fetch non-public URL: {url}')
        return b''

    kwargs.setdefault('timeout', 8)
    try:
        resp = http_session().get(url, stream=True, **kwargs)
    except requests.exceptions.RequestException:
        return b''
    try:
        if not resp.ok:
            return b''
        clen = resp.headers.get('Content-Length')
        if clen and clen.isdigit() and int(clen) > max_bytes:
            bsn_logger.warning(f'Remote image too large ({clen} B): {url}')
            return b''
        buf = bytearray()
        for chunk in resp.iter_content(8192):
            buf += chunk
            if len(buf) > max_bytes:
                bsn_logger.warning(f'Remote image exceeded {max_bytes} B: {url}')
                return b''
        return bytes(buf)
    finally:
        resp.close()
