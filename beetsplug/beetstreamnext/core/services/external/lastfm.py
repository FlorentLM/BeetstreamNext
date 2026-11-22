from __future__ import annotations

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.core.services.external import ExternalService


class LastFM(ExternalService):
    """
    Last.fm metadata (bios, similar artists/tracks, top tracks).
    Note: scrobbling stuff is in the dedicated scrobbling/lastfm.py.
    """

    label = 'Last.fm'
    timeout = 15    # Last.fm is slow...
    api_url = 'https://ws.audioscrobbler.com/2.0/'

    def query(self, q: str, data_type: str, method: str = 'info', is_mbid: bool = True, artist: str = '') -> dict:

        if not app.config['lastfm_api_key']:
            return {}

        params = {
            'format': 'json',
            'method': f'{data_type}.get{method.title()}',
            'api_key': app.config['lastfm_api_key'],
        }

        if is_mbid:
            params['mbid'] = q.replace(' ', '+')
        elif q and data_type != 'user':
            params[data_type] = q
            # track.* methods need both artist and track name to disambiguate
            if artist and data_type == 'track':
                params['artist'] = artist

        return self.get_json(self.api_url, params=params)

    def test_connection(self) -> tuple[bool, str]:
        """Check that the configured API key is valid. Returns (ok, message)."""

        api_key = settings_store.get('lastfm_api_key')
        if not api_key:
            return False, 'No Last.fm API key configured.'

        params = {'format': 'json', 'method': 'chart.gettopartists', 'api_key': api_key, 'limit': 1}
        response = self.request('GET', self.api_url, params=params, timeout=8)
        if response is None:
            return False, 'Could not reach Last.fm.'

        try:
            payload = response.json()
        except ValueError:
            payload = None

        if isinstance(payload, dict) and payload.get('error'):
            return False, payload.get('message', 'Last.fm rejected the API key.')
        if not response.ok:
            return False, f'Last.fm returned HTTP {response.status_code}.'

        return True, 'Connected to Last.fm successfully.'


lastfm_service = LastFM()
