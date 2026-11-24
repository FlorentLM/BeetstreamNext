from __future__ import annotations

from typing import Any
from collections.abc import Iterator, Sequence
from urllib.parse import quote

from beetsplug.beetstreamnext.constants import SERVER_NAME, SERVER_VERSION
from beetsplug.beetstreamnext.core.services.scrobbling.base import (
    RemoteListen, ScrobbleError, ScrobbleTrack, ScrobblerBase, ScrobbleStatus
)
from beetsplug.beetstreamnext.utils.text import validate_mbid


DEFAULT_URL = 'https://api.listenbrainz.org'
PAGE_SIZE = 1000            # items returned per GET


class ListenBrainzScrobbler(ScrobblerBase):
    service = 'listenbrainz'
    label = 'ListenBrainz'

    credentials_url = 'https://listenbrainz.org/settings/'
    default_url = DEFAULT_URL

    @staticmethod
    def base_url(credentials: dict[str, Any]) -> str:
        return (credentials.get('url') or DEFAULT_URL).rstrip('/')

    def verify(self, credentials: dict[str, Any]) -> str:

        response = self.request(
            'GET', f'{self.base_url(credentials)}/1/validate-token',
            headers={'Authorization': f"Token {credentials['token']}"}
        )

        if response is None:
            raise ScrobbleError(
                'Could not reach ListenBrainz. Check the URL (a server on a private network '
                'has to be allowed by an admin first).'
            )

        body = self.json_body(response)
        if not response.ok or not body.get('valid'):
            raise ScrobbleError('ListenBrainz did not accept this token.')

        return str(body.get('user_name') or '')

    @staticmethod
    def _listen(track: ScrobbleTrack, with_time: bool) -> dict[str, Any]:

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

    def _post(self, credentials: dict[str, Any], path: str, body: dict[str, Any]) -> ScrobbleStatus:

        response = self.request(
            'POST', f'{self.base_url(credentials)}{path}', json=body,
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

    def now_playing(self, credentials: dict[str, Any], track: ScrobbleTrack) -> ScrobbleStatus:

        body = {
            'listen_type': 'playing_now',
            'payload': [self._listen(track, with_time=False)]
        }

        return self._post(credentials, '/1/submit-listens', body)

    def submit(self, credentials: dict[str, Any], tracks: Sequence[ScrobbleTrack]) -> ScrobbleStatus:

        listens = [self._listen(t, with_time=True) for t in tracks]

        body = {
            'listen_type': 'single' if len(listens) == 1 else 'import',
            'payload': listens
        }

        return self._post(credentials, '/1/submit-listens', body)

    def push_feedback(self, credentials: dict[str, Any], track: ScrobbleTrack, score: int) -> ScrobbleStatus:

        if not track.recording_mbid:
            return ScrobbleStatus.SKIP      # ListenBrainz needs MusicBrainz recording ID for that

        body = {
            'recording_mbid': track.recording_mbid,
            'score': score
        }

        return self._post(credentials, '/1/feedback/recording-feedback', body)

    def _get(self, credentials: dict[str, Any], path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET a JSON object. Raises ScrobbleError."""

        response = self.request(
            'GET', f'{self.base_url(credentials)}{path}', params=params,
            headers={'Authorization': f"Token {credentials['token']}"}
        )

        if response is None:
            raise ScrobbleError('Could not reach ListenBrainz.')
        if response.status_code == 429:
            raise ScrobbleError('ListenBrainz is limiting requests, try again in a few minutes.')
        if response.status_code == 401:
            raise ScrobbleError('ListenBrainz did not accept the token.')
        if response.status_code == 404:
            raise ScrobbleError('ListenBrainz does not know this account.')
        if not response.ok:
            raise ScrobbleError(f'ListenBrainz answered with an error ({response.status_code}).')

        body = self.json_body(response)
        if not body:
            raise ScrobbleError('ListenBrainz sent an unexpected answer (is the URL a ListenBrainz server?).')

        return body

    @staticmethod
    def _parse_listen(metadata: Any, at: Any, mbid: Any = '') -> RemoteListen | None:

        metadata = metadata if isinstance(metadata, dict) else {}
        info = metadata.get('additional_info') or {}
        mapping = metadata.get('mbid_mapping') or {}

        remote = RemoteListen(
            artist=str(metadata.get('artist_name') or '').strip(),
            title=str(metadata.get('track_name') or '').strip(),
            album=str(metadata.get('release_name') or '').strip(),
            recording_mbid=validate_mbid(mbid or info.get('recording_mbid') or mapping.get('recording_mbid')),
            at=int(at or 0),
        )

        if not remote.recording_mbid and not (remote.artist and remote.title):
            return None

        return remote

    def pull_listens(self, credentials: dict[str, Any], account: str, before: int) -> list[RemoteListen]:

        body = self._get(credentials, f'/1/user/{quote(account, safe="")}/listens', {'max_ts': before, 'count': PAGE_SIZE})
        listens = (body.get('payload') or {}).get('listens') or []

        remotes = []
        for listen in listens:
            if not isinstance(listen, dict):
                continue
            remote = self._parse_listen(listen.get('track_metadata'), listen.get('listened_at'))
            if remote:
                remotes.append(remote)
        return remotes

    def pull_feedback(self, credentials: dict[str, Any], account: str) -> Iterator[RemoteListen]:

        offset = 0
        while True:

            body = self._get(
                credentials, f'/1/feedback/user/{quote(account, safe="")}/get-feedback',
                {'count': PAGE_SIZE, 'offset': offset, 'metadata': 'true'}
            )
            feedback = body.get('feedback') or []

            for entry in feedback:
                if isinstance(entry, dict) and entry.get('score') in (1, -1):
                    remote = self._parse_listen(entry.get('track_metadata'), entry.get('created'), entry.get('recording_mbid'))
                    if remote:
                        remote.score = entry['score']
                        yield remote

            if len(feedback) < PAGE_SIZE:
                return

            offset += len(feedback)
