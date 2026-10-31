from __future__ import annotations

from typing import Any, Dict, Sequence

from beetsplug.beetstreamnext.constants import SERVER_NAME, SERVER_VERSION
from beetsplug.beetstreamnext.core.services.scrobbling.base import ScrobbleError, ScrobbleTrack, ScrobblerBase, ScrobbleStatus


DEFAULT_URL = 'https://api.listenbrainz.org'


class ListenBrainzScrobbler(ScrobblerBase):
    service = 'listenbrainz'
    label = 'ListenBrainz'

    credentials_url = 'https://listenbrainz.org/settings/'
    default_url = DEFAULT_URL

    @staticmethod
    def base_url(credentials: Dict[str, Any]) -> str:
        return (credentials.get('url') or DEFAULT_URL).rstrip('/')

    def verify(self, credentials: Dict[str, Any]) -> str:

        response = self.request(
            'GET', f'{self.base_url(credentials)}/1/validate-token',
            headers={'Authorization': f"Token {credentials['token']}"}
        )
        if response is None:
            raise ScrobbleError('Could not reach ListenBrainz.')

        try:
            body = response.json()
        except ValueError:
            body = {}

        if not response.ok or not isinstance(body, dict) or not body.get('valid'):
            raise ScrobbleError('ListenBrainz did not accept this token.')

        return str(body.get('user_name') or '')

    @staticmethod
    def _listen(track: ScrobbleTrack, with_time: bool) -> Dict[str, Any]:

        info = {
            'media_player': SERVER_NAME,
            'submission_client': SERVER_NAME,
            'submission_client_version': SERVER_VERSION,
        }

        if track.recording_mbid:
            info['recording_mbid'] = track.recording_mbid
        if track.release_mbid:
            info['release_mbid'] = track.release_mbid
        if track.artist_mbids:
            info['artist_mbids'] = list(track.artist_mbids)
        if track.track_number:
            info['tracknumber'] = track.track_number
        if track.duration_s:
            info['duration_ms'] = track.duration_s * 1000

        metadata = {'artist_name': track.artist, 'track_name': track.title, 'additional_info': info}
        if track.album:
            metadata['release_name'] = track.album

        listen = {'track_metadata': metadata}
        if with_time:
            listen['listened_at'] = int(track.played_at)

        return listen

    def _post(self, credentials: Dict[str, Any], listen_type: str, listens: list) -> ScrobbleStatus:

        response = self.request(
            'POST', f'{self.base_url(credentials)}/1/submit-listens',
            json={'listen_type': listen_type, 'payload': listens},
            headers={'Authorization': f"Token {credentials['token']}"}
        )

        if response is None:
            return ScrobbleStatus.RETRY
        if response.ok:
            return ScrobbleStatus.OK
        if response.status_code == 401:
            return ScrobbleStatus.INVALID_AUTH
        if response.status_code == 429 or response.status_code >= 500:
            return ScrobbleStatus.RETRY
        return ScrobbleStatus.DROP

    def now_playing(self, credentials: Dict[str, Any], track: ScrobbleTrack) -> ScrobbleStatus:
        listens = [self._listen(track, with_time=False)]
        listen_type = 'playing_now'
        return self._post(credentials, listen_type, listens)

    def submit(self, credentials: Dict[str, Any], tracks: Sequence[ScrobbleTrack]) -> ScrobbleStatus:
        listens = [self._listen(t, with_time=True) for t in tracks]
        return self._post(credentials, 'single' if len(listens) == 1 else 'import', listens)
