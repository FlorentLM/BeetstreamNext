from __future__ import annotations

import time
import threading
from datetime import timedelta
import requests
from requests.adapters import HTTPAdapter
from requests_cache import CachedSession

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import MAX_REMOTE_IMAGE_BYTES, USER_AGENT
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.core.security import is_public_url
from beetsplug.beetstreamnext.utils.net import https_variant, SCHEME_RE, DUPLICATE_SCHEME_RE


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


_http_session = None


def http_session() -> CachedSession:
    global _http_session

    if _http_session is None:
        _http_session = CachedSession(
            str(app.config['HTTP_CACHE_PATH']),
            backend='sqlite',
            expire_after=timedelta(days=30),
            allowable_codes=[200],
            stale_if_error=True     # serve expired cached version if remote server goes down
        )

        _http_session.headers.update({'User-Agent': USER_AGENT})

        # MusicBrainz's courtesy limit is ~50 req/s, we throttle at 25 req/s
        # https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting
        musicbrainz_adapter = RequestThrottle(min_interval=0.04)
        _http_session.mount('https://musicbrainz.org', musicbrainz_adapter)
        _http_session.mount('http://musicbrainz.org', musicbrainz_adapter)
    return _http_session


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

    if probe_https and url.lower().startswith('http://'):
        https_url = https_variant(url)
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
