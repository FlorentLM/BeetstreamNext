from __future__ import annotations

import requests

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import USER_AGENT
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.core.services.external.session import http_session


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
