import re
import urllib.parse
from datetime import timedelta
from functools import lru_cache
from typing import Optional, Dict, List, Any
import requests
import asyncio
from requests_cache import CachedSession

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import (
    WIKI_API, RADIO_BROWSER, MAX_REMOTE_IMAGE_BYTES, USER_AGENT, _SCHEME_RE, _DUPLICATE_SCHEME_RE
)
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.settings import settings_store


def https_variant(url: str) -> str:
    """Returns url with its scheme flipped between http and https, or url unchanged if neither."""
    if url.lower().startswith('http://'):
        return 'https://' + url[len('http://'):]
    if url.lower().startswith('https://'):
        return 'http://' + url[len('https://'):]
    return url


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

    url = _DUPLICATE_SCHEME_RE.sub('', url)

    if not _SCHEME_RE.match(url):
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


_ICON_LINK_RE = re.compile(
    r'<link[^>]+rel=["\'](?:shortcut icon|icon|apple-touch-icon(?:-precomposed)?)["\'][^>]*>',
    re.IGNORECASE
)
_ICON_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_ICON_SIZES_RE = re.compile(r'sizes=["\'](\d+)x\d+["\']', re.IGNORECASE)

_DEEZER_PLACEHOLDER_HASHES = frozenset({
    'd41d8cd98f00b204e9800998ecf8427e',
})


def capped_image_fetch(url: str, *, max_bytes: int = MAX_REMOTE_IMAGE_BYTES, **kwargs) -> bytes:
    """GET image bytes, refusing bodies over max_bytes. Returns b'' on failure."""
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


def _is_deezer_placeholder(artist_data: Dict) -> bool:
    url = artist_data.get('picture_small', '')

    if '//56x56' in url or '//250x250' in url:
        return True

    for h in _DEEZER_PLACEHOLDER_HASHES:
        if h in url:
            return True
    return not bool(url)


def query_deezer(artist: Optional[str] = None, album: Optional[str] = None) -> dict:

    if not artist and not album:
        return {}

    artist = str(artist) if artist else ''
    album = str(album) if album else ''
    artist_quot = urllib.parse.quote_plus(artist)
    album_quot = urllib.parse.quote_plus(album)

    base_search = 'https://api.deezer.com/search/'

    if artist_quot and album_quot:
        search_endpoint = base_search + f'?q=artist:"{artist_quot}" album:"{album_quot}"'
    elif artist_quot:
        search_endpoint = base_search + f'artist?q={artist_quot}'
    elif album_quot:
        search_endpoint = base_search + f'album?q={album_quot}'

    search_endpoint += '&limit=5&index=0'

    headers = {'User-Agent': USER_AGENT}

    try:
        response = http_session().get(search_endpoint, headers=headers, timeout=8)
        if response.from_cache:
            bsn_logger.debug(f"Cache hit for Deezer: {artist}")

        if response.ok:
            candidates = response.json().get('data', [])

            if candidates and artist:
                # Prefer exact name matches
                exact_matches = [c for c in candidates if c.get('name', '').lower() == artist.lower()]
                pool = exact_matches if exact_matches else candidates
                if len(pool) == 1:
                    return pool[0]

                # Prefer candidates with a real image
                with_image = [c for c in pool if not _is_deezer_placeholder(c)]
                pool = with_image if with_image else pool
                if len(pool) == 1:
                    return pool[0]

                # Last resort take the one with highest nb_fan
                return max(pool, key=lambda c: c.get('nb_fan', 0))

    except requests.exceptions.RequestException:
        pass

    return {}


def query_musicbrainz(mbid: str, data_type: str) -> dict:

    types_mb = {'track': 'recording', 'album': 'release', 'artist': 'artist'}
    endpoint = f'https://musicbrainz.org/ws/2/{types_mb[data_type]}/{mbid}'

    headers = {'User-Agent': USER_AGENT}
    params = {'fmt': 'json'}

    if types_mb[data_type] == 'artist':
        params['inc'] = 'annotation'

    try:
        response = http_session().get(endpoint, headers=headers, params=params, timeout=8)
        if response.from_cache:
            bsn_logger.debug(f"Cache hit for MusicBrainz: {mbid}")
        return response.json() if response.ok else {}

    except requests.exceptions.RequestException:
        return {}


def query_discogs(release_id: Any) -> dict:
    """
    Fetch a Discogs release.
    Unauthenticated version so 25 req/min, maximum.
    """
    if not release_id:
        return {}

    endpoint = f'https://api.discogs.com/releases/{release_id}'
    headers = {'User-Agent': USER_AGENT}

    try:
        response = http_session().get(endpoint, headers=headers, timeout=8)
        if response.from_cache:
            bsn_logger.debug(f"Cache hit for Discogs: {release_id}")
        return response.json() if response.ok else {}

    except requests.exceptions.RequestException:
        return {}


def query_lastfm(q: str, data_type: str, method: str = 'info', is_mbid: bool = True, artist: str = '') -> dict:

    if not app.config['lastfm_api_key']:
        return {}

    endpoint = 'https://ws.audioscrobbler.com/2.0/'

    params = {
        'format': 'json',
        'method': f'{data_type}.get{method.title()}',
        'api_key': app.config['lastfm_api_key'],
        }

    if is_mbid:
        q = q.replace(' ', '+')
        params['mbid'] = q
    elif q and data_type != 'user':
        params[data_type] = q
        # track.* methods need both artist and track name to disambiguate
        if artist and data_type == 'track':
            params['artist'] = artist

    headers = {'User-Agent': USER_AGENT}
    try:
        response = http_session().get(endpoint, headers=headers, params=params, timeout=15) # lastfm is very slow...
        if response.from_cache:
            bsn_logger.debug(f"Cache hit for Last.fm: {q}")
        return response.json() if response.ok else {}

    except requests.exceptions.RequestException:
        return {}


def test_lastfm_connection() -> tuple[bool, str]:
    """Check that the configured Last.fm API key is valid. Returns (ok, message)."""
    api_key = settings_store.get('lastfm_api_key')
    if not api_key:
        return False, 'No Last.fm API key configured.'

    endpoint = 'https://ws.audioscrobbler.com/2.0/'
    params = {'format': 'json', 'method': 'chart.gettopartists', 'api_key': api_key, 'limit': 1}
    headers = {'User-Agent': USER_AGENT}

    try:
        response = requests.get(endpoint, headers=headers, params=params, timeout=8)
    except requests.exceptions.RequestException as e:
        return False, f'Could not reach Last.fm: {e}'

    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict) and payload.get('error'):
        return False, payload.get('message', 'Last.fm rejected the API key.')
    if not response.ok:
        return False, f'Last.fm returned HTTP {response.status_code}.'

    return True, 'Connected to Last.fm successfully.'


def _audiomuse_get(path: str, params: dict, timeout: float = 10.0):
    """GET a AudioMuse-AI endpoint. Returns (data, error_message)."""
    audiomuse_url = settings_store.get('audiomuse_url')
    if not audiomuse_url:
        return None, 'AudioMuse-AI is not configured on this server.'

    headers = {}
    api_token = settings_store.get('audiomuse_api_token')
    if api_token:
        headers['Authorization'] = f'Bearer {api_token}'

    try:
        url = f"{audiomuse_url.rstrip('/')}{path}"
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        if not response.ok:
            bsn_logger.error(f'AudioMuse-AI API error: {response.status_code} - {response.text}')
            return None, 'Failed to communicate with AudioMuse-AI.'
        return response.json(), None

    except requests.RequestException as e:
        bsn_logger.error(f'AudioMuse-AI connection failed: {e}')
        return None, 'Could not connect to AudioMuse-AI.'


def _audiomuse_post(path: str, json_body: dict, timeout: float = 10.0):
    """POST to a AudioMuse-AI endpoint. Returns (data, error_message)."""
    audiomuse_url = settings_store.get('audiomuse_url')
    if not audiomuse_url:
        return None, 'AudioMuse-AI is not configured on this server.'

    headers = {}
    api_token = settings_store.get('audiomuse_api_token')
    if api_token:
        headers['Authorization'] = f'Bearer {api_token}'

    try:
        url = f"{audiomuse_url.rstrip('/')}{path}"
        response = requests.post(url, json=json_body, headers=headers, timeout=timeout)
        if not response.ok:
            bsn_logger.error(f'AudioMuse-AI API error: {response.status_code} - {response.text}')
            return None, 'Failed to communicate with AudioMuse-AI.'
        return response.json(), None

    except requests.RequestException as e:
        bsn_logger.error(f'AudioMuse-AI connection failed: {e}')
        return None, 'Could not connect to AudioMuse-AI.'


def test_audiomuse_connection() -> tuple[bool, str]:
    """Ping AudioMuse-AI's health endpoint. Returns (ok, message)."""
    if not settings_store.get('audiomuse_url'):
        return False, 'AudioMuse-AI URL is not configured.'

    data, err = _audiomuse_get('/api/health', {}, timeout=8.0)
    if err:
        return False, err
    return True, 'Connected to AudioMuse-AI successfully.'


def start_audiomuse_analysis() -> tuple[bool, str]:
    """Trigger a AudioMuse-AI analysis over the whole library."""
    data, err = _audiomuse_post('/api/analysis/start', {'num_recent_albums': 0}, timeout=15.0)
    if err:
        return False, err

    task_id = (data or {}).get('task_id')
    if task_id:
        return True, f'Fingerprinting started on AudioMuse-AI (task {task_id}).'
    return True, 'Fingerprinting started on AudioMuse-AI.'


async def _async_wiki_search(q: str) -> str | None:

    if WIKI_API:
        import wikipediaapi
    else:
        return None

    wiki = wikipediaapi.AsyncWikipedia(user_agent=USER_AGENT, language='en', timeout=8)
    page = wiki.page(q)

    if await page.exists():
        summary = await page.summary
        return summary

    return None


@lru_cache(maxsize=512)
def query_wikipedia(q: str, _cache_ttl_hash=None) -> str | None:
    """`_cache_ttl_hash` is just to change the function signature every x seconds to inactivate the lru."""

    if not WIKI_API:
        return None

    from beetsplug.beetstreamnext.utils.text import standard_ascii
    from beetsplug.beetstreamnext.utils.text import remove_accents
    q = standard_ascii(q)
    q = remove_accents(q)
    if not q:
        return None

    try:
        return asyncio.run(_async_wiki_search(q))
    except Exception as e:
        bsn_logger.error(f'Wikipedia query failed: {e}')
        return None


def query_coverartarchive(mbid: str) -> bytes:
    """Fetch image from CAA (size-capped) and cache the bytes. Returns b'' if not found to avoid retries."""
    if not mbid:
        return b''
    return capped_image_fetch(f'https://coverartarchive.org/release/{mbid}/front')


async def _async_radio_search(query: str, limit: int) -> List[dict]:

    from radios import RadioBrowser

    async with RadioBrowser(user_agent=USER_AGENT) as rb:

        # Searching by name
        stations = await rb.search(name=query, limit=limit)
        return [{
            'name': s.name,
            'stream_url': s.url,
            'homepage_url': s.homepage,
            'favicon': s.favicon,
        } for s in stations]


def fetch_favicon(homepage_url: str) -> bytes:
    """
    Fetch a site's icon. Prefers the largest <link rel="icon"/"apple-touch-icon"> declared
    in the page's html, falling back to DuckDuckGo's favicon proxy if nothing usable was found.
    Returns b'' on failure.
    """
    domain = urllib.parse.urlparse(homepage_url).netloc
    if not domain:
        return b''

    best_url, best_size = None, 0
    try:
        resp = http_session().get(homepage_url, timeout=8, headers={'User-Agent': USER_AGENT})
        if resp.ok:
            for tag in _ICON_LINK_RE.findall(resp.text):
                href = _ICON_HREF_RE.search(tag)
                if not href:
                    continue
                size_match = _ICON_SIZES_RE.search(tag)
                size = int(size_match.group(1)) if size_match else 32  # unspecified ~= favicon-grade
                if size > best_size:
                    best_url, best_size = urllib.parse.urljoin(homepage_url, href.group(1)), size
    except requests.exceptions.RequestException:
        pass

    if best_url:
        image = capped_image_fetch(best_url)
        if image:
            return image

    return capped_image_fetch(f'https://icons.duckduckgo.com/ip3/{domain}.ico')


def query_radio_browser(q: str, limit: int = 20) -> list:

    if not RADIO_BROWSER:
        return []

    try:
        return asyncio.run(_async_radio_search(q, limit))
    except Exception as e:
        bsn_logger.error(f'Radio Browser query failed: {e}')
        return []