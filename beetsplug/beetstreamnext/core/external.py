import urllib.parse
from datetime import timedelta
from functools import lru_cache
from typing import Optional, Dict
import requests
from requests_cache import CachedSession

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import WIKI_API, BEETSTREAMNEXT_VER, bsn_logger, MAX_REMOTE_IMAGE_BYTES

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

    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VER} ( https://github.com/FlorentLM/BeetstreamNext )'}

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


def query_musicbrainz(mbid: str, type: str) -> dict:

    types_mb = {'track': 'recording', 'album': 'release', 'artist': 'artist'}
    endpoint = f'https://musicbrainz.org/ws/2/{types_mb[type]}/{mbid}'

    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VER} ( https://github.com/FlorentLM/BeetstreamNext )'}
    params = {'fmt': 'json'}

    if types_mb[type] == 'artist':
        params['inc'] = 'annotation'

    try:
        response = http_session().get(endpoint, headers=headers, params=params, timeout=8)
        if response.from_cache:
            bsn_logger.debug(f"Cache hit for MusicBrainz: {mbid}")
        return response.json() if response.ok else {}

    except requests.exceptions.RequestException:
        return {}


def query_lastfm(q: str, type: str, method: str = 'info', is_mbid: bool = True) -> dict:

    if not app.config['lastfm_api_key']:
        return {}

    endpoint = 'https://ws.audioscrobbler.com/2.0/'

    params = {
        'format': 'json',
        'method': f'{type}.get{method.title()}',
        'api_key': app.config['lastfm_api_key'],
        }

    if is_mbid:
        q = q.replace(' ', '+')
        params['mbid'] = q
    elif q and type != 'user':
        params[type] = q

    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VER} ( https://github.com/FlorentLM/BeetstreamNext )'}
    try:
        response = http_session().get(endpoint, headers=headers, params=params, timeout=15) # lastfm is very slow...
        if response.from_cache:
            bsn_logger.debug(f"Cache hit for Last.fm: {q}")
        return response.json() if response.ok else {}

    except requests.exceptions.RequestException:
        return {}


@lru_cache(maxsize=512)
def query_wikipedia(q: str, cache_ttl_hash=None) -> str | None:
    """`cache_ttl_hash` is just to change the function signature every x seconds to inactivate the lru."""

    if not WIKI_API:
        return None

    import wikipediaapi

    from beetsplug.beetstreamnext.utils.text import standard_ascii
    from beetsplug.beetstreamnext.utils.text import remove_accents
    q = standard_ascii(q)
    q = remove_accents(q)
    if not q:
        return None

    user_agent = f'BeetstreamNext/{BEETSTREAMNEXT_VER} ( https://github.com/FlorentLM/BeetstreamNext )'
    wiki = wikipediaapi.Wikipedia(user_agent=user_agent, language='en', timeout=8)
    page = wiki.page(q)

    if page.exists():
        return page.summary

    return None


def query_coverartarchive(mbid: str) -> bytes:
    """Fetch image from CAA (size-capped) and cache the bytes. Returns b'' if not found to avoid retries."""
    if not mbid:
        return b''
    return capped_image_fetch(f'https://coverartarchive.org/release/{mbid}/front')
