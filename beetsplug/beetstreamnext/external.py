import urllib.parse
from datetime import timedelta
from functools import lru_cache
from typing import Optional, Dict
import requests
import requests_cache
from requests import RequestException

from beetsplug.beetstreamnext import app

BEETSTREAMNEXT_VERSION = '1.6.0-dev'

try:
    import wikipediaapi
    WIKI_API = True
except ImportError:
    WIKI_API = False


http_session = requests_cache.CachedSession(
    str(app.config['HTTP_CACHE_PATH']),
    backend='sqlite',
    expire_after=timedelta(days=30),
    allowable_codes=[200],
    stale_if_error=True         # serve expired cached version if remote server goes down
)


def query_musicbrainz(mbid: str, type: str):

    types_mb = {'track': 'recording', 'album': 'release', 'artist': 'artist'}
    endpoint = f'https://musicbrainz.org/ws/2/{types_mb[type]}/{mbid}'

    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'}
    params = {'fmt': 'json'}

    if types_mb[type] == 'artist':
        params['inc'] = 'annotation'

    try:
        response = http_session.get(endpoint, headers=headers, params=params, timeout=8)
        if response.from_cache:
            app.logger.debug(f"Cache hit for MusicBrainz: {mbid}")
        return response.json() if response.ok else {}

    except requests.exceptions.RequestException:
        return {}


def query_deezer(artist: Optional[str] = None, album: Optional[str] = None) -> Dict:

    if artist:
        artist = urllib.parse.quote_plus(artist)
    if album:
        album = urllib.parse.quote_plus(album)

    if not artist and not album:
        return {}

    base_search = 'https://api.deezer.com/search/'

    if artist and album:
        search_endpoint = base_search + f'?q=artist:"{artist}" album:"{album}"'
    elif artist:
        search_endpoint = base_search + f'artist?q={artist}'
    elif album:
        search_endpoint = base_search + f'album?q={album}'

    search_endpoint += '&limit=1&index=0'
    # TODO: Actually Deezer's API sometimes return a duplicate (wrong) entry with same artist name.
    #   Maybe fix: use the 'nb_fan' entry to disambiguate?
    #   Example 'Mariah Carey' axists as nb_fan: 58 and nb_fan: 3404526, obviously the real one is the 2nd

    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'}

    try:
        response = http_session.get(search_endpoint, headers=headers, timeout=8)
        if response.from_cache:
            app.logger.debug(f"Cache hit for Deezer: {artist}")
        if response.ok:
            data = response.json().get('data', {})
            if data:
                return data[0]
    except requests.exceptions.RequestException:
        return {}

    return {}


def query_lastfm(q: str, type: str, method: str = 'info', is_mbid=True) -> Dict:

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

    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'}
    try:
        response = http_session.get(endpoint, headers=headers, params=params, timeout=15) # lastfm is very slow...
        if response.from_cache:
            app.logger.debug(f"Cache hit for Last.fm: {q}")
        return response.json() if response.ok else {}

    except requests.exceptions.RequestException:
        return {}


@lru_cache(maxsize=512)
def query_wikipedia(q: str) -> Optional[str]:
    if not WIKI_API:
        return None

    from beetsplug.beetstreamnext.utils import standard_ascii
    q = standard_ascii(q)
    if not q:
        return None

    user_agent = f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'
    wiki = wikipediaapi.Wikipedia(user_agent=user_agent, language='en', timeout=8)
    page = wiki.page(q)

    if page.exists():
        return page.summary

    return None


def query_coverartarchive(mbid: str) -> bytes:
    """Fetch image from CAA and cache the bytes. Returns b'' if not found to avoid retries."""
    if not mbid:
        return b''

    art_url = f'https://coverartarchive.org/release/{mbid}/front'
    try:
        response = http_session.get(art_url, timeout=8)
        if response.from_cache:
            app.logger.debug(f"Cache hit for Cover Art Archive: {mbid}")

        return response.content if (response.ok and response.content) else b''

    except RequestException:
        return b''
