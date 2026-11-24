from __future__ import annotations

from typing import Any
from collections.abc import Iterator, Sequence

from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.core.services.scrobbling.base import (
    RemoteListen, ScrobbleError, ScrobbleTrack, ScrobblerBase, ScrobbleStatus
)
from beetsplug.beetstreamnext.utils.text import md5_hex, strip_text, validate_mbid


INVALID_AUTH_CODES = {4, 9, 10, 26}     # Bad token or session, bad key, suspended key
RETRY_CODES = {11, 16, 29}              # Service offline, temporary error, rate limit
PAGE_SIZE = 200                         # Max tracks per page for user.getRecentTracks


def sign(params: dict[str, Any], secret: str) -> str:
    """MD5 of all the params (excl. `format`) as `name+value`, + the secret."""
    raw = ''.join(f'{k}{params[k]}' for k in sorted(params) if k != 'format')
    return md5_hex(raw + secret)


class LastFMScrobbler(ScrobblerBase):

    auth_kind = 'webauth'
    has_dislikes = False
    batch_size = 50

    def __init__(self, service: str, label: str, api_url: str, auth_url: str) -> None:
        self.service, self.label = service, label
        self.api_url, self.auth_url = api_url, auth_url

    @staticmethod
    def _key() -> str:
        return settings_store.get('lastfm_api_key') or ''

    @staticmethod
    def _secret() -> str:
        return settings_store.get('lastfm_shared_secret') or ''

    def available(self) -> bool:
        return bool(self._key() and self._secret())

    def _call(self, method: str, params: dict[str, Any], session_key: str = '') -> tuple[ScrobbleStatus, dict]:
        """Signed POST. Returns the status and the decoded body."""

        data = {**params, 'method': method, 'api_key': self._key()}
        if session_key:
            data['sk'] = session_key

        data['api_sig'] = sign(data, self._secret())
        data['format'] = 'json'

        response = self.request('POST', self.api_url, data=data)
        if response is None:
            return ScrobbleStatus.RETRY, {}

        body = self.json_body(response)

        if 'error' in body:
            try:
                code = int(body['error'])
            except (TypeError, ValueError):
                code = 0
            if code in INVALID_AUTH_CODES:
                return ScrobbleStatus.INVALID_AUTH, body
            if code in RETRY_CODES:
                return ScrobbleStatus.RETRY, body
            return ScrobbleStatus.DROP, body

        if not response.ok:
            retry = response.status_code == 429 or response.status_code >= 500
            return (ScrobbleStatus.RETRY if retry else ScrobbleStatus.DROP), body

        return ScrobbleStatus.OK, body

    ##
    # Auth

    def start_auth(self) -> tuple[str, str]:

        status, body = self._call('auth.getToken', {})
        token = str(body.get('token') or '')
        if status is not ScrobbleStatus.OK or not token:
            raise ScrobbleError(f'Could not start the {self.label} authorisation. Check the API key and shared secret.')

        return token, self.auth_page(token)

    def auth_page(self, pending_token: str) -> str:
        return f'{self.auth_url}?api_key={self._key()}&token={pending_token}'

    def finish_auth(self, pending_token: str) -> tuple[dict[str, Any], str]:

        status, body = self._call('auth.getSession', {'token': pending_token})
        session = body.get('session') if isinstance(body.get('session'), dict) else {}
        key = str(session.get('key') or '')

        if status is not ScrobbleStatus.OK or not key:
            raise ScrobbleError(f"{self.label} did not confirm the authorisation. Approve it on {self.label} first.")

        return {'session_key': key}, str(session.get('name') or '')

    def verify(self, credentials: dict[str, Any]) -> str:

        status, body = self._call('user.getInfo', {}, credentials.get('session_key', ''))
        if status is not ScrobbleStatus.OK:
            raise ScrobbleError(f'{self.label} did not accept this session.')

        user = body.get('user')
        return str(user.get('name') or '') if isinstance(user, dict) else ''

    ##
    # Listens

    @staticmethod
    def _track_params(track: ScrobbleTrack, suffix: str = '') -> dict[str, Any]:

        params: dict[str, Any] = {f'artist{suffix}': track.artist, f'track{suffix}': track.title}
        if track.album:
            params[f'album{suffix}'] = track.album
        if track.duration_s:
            params[f'duration{suffix}'] = track.duration_s
        if track.track_number:
            params[f'trackNumber{suffix}'] = track.track_number
        if track.recording_mbid:
            params[f'mbid{suffix}'] = track.recording_mbid
        return params

    def now_playing(self, credentials: dict[str, Any], track: ScrobbleTrack) -> ScrobbleStatus:
        status, _ = self._call('track.updateNowPlaying', self._track_params(track), credentials.get('session_key', ''))
        return status

    def submit(self, credentials: dict[str, Any], tracks: Sequence[ScrobbleTrack]) -> ScrobbleStatus:

        params: dict[str, Any] = {}
        for i, track in enumerate(tracks[:self.batch_size]):
            params.update(self._track_params(track, f'[{i}]'))
            params[f'timestamp[{i}]'] = int(track.played_at)

        status, _ = self._call('track.scrobble', params, credentials.get('session_key', ''))
        return status

    def push_feedback(self, credentials: dict[str, Any], track: ScrobbleTrack, score: int) -> ScrobbleStatus:
        """Send likes (no dislikes on Last.fm, a dislike here only unlikes on Last.fm)."""

        if not (track.artist and track.title):
            return ScrobbleStatus.SKIP

        method = 'track.love' if score == 1 else 'track.unlove'
        status, _ = self._call(method, {'artist': track.artist, 'track': track.title}, credentials.get('session_key', ''))
        return status


    ##
    # Import

    def _read(self, credentials: dict[str, Any], method: str, params: dict[str, Any], key: str) -> tuple[list, dict]:

        status, body = self._call(method, params, credentials.get('session_key', ''))

        if status is ScrobbleStatus.INVALID_AUTH:
            raise ScrobbleError(f'{self.label} did not accept the session, reconnect it.')
        if status is ScrobbleStatus.RETRY:
            raise ScrobbleError(f'Could not reach {self.label} (or it is limiting requests), try again in a few minutes.')
        if status is not ScrobbleStatus.OK:
            raise ScrobbleError(f"{self.label} answered with an error ({body.get('error', 'HTTP')}).")

        container = body.get(key)
        if not isinstance(container, dict):
            raise ScrobbleError(f'{self.label} sent an unexpected answer.')

        tracks = container.get('track') or []
        if isinstance(tracks, dict):
            tracks = [tracks]       # A single track comes without the list

        attr = container.get('@attr')

        return tracks, attr if isinstance(attr, dict) else {}

    @staticmethod
    def _parse_track(entry: Any) -> RemoteListen | None:

        if not isinstance(entry, dict) or (entry.get('@attr') or {}).get('nowplaying'):
            return None

        try:
            at = int((entry.get('date') or {}).get('uts') or 0)
        except (AttributeError, TypeError, ValueError):
            at = 0

        artist = entry.get('artist') or {}    # Name in '#text' (recent tracks) or 'name' (loved tracks)

        remote = RemoteListen(
            artist=strip_text(artist.get('#text') or artist.get('name')),
            title=strip_text(entry.get('name')),
            album=strip_text((entry.get('album') or {}).get('#text')),
            recording_mbid=validate_mbid(entry.get('mbid')),
            at=at,
        )

        if not (remote.artist and remote.title):
            return None

        return remote

    def pull_listens(self, credentials: dict[str, Any], account: str, before: int) -> list[RemoteListen]:

        params = {'user': account, 'limit': PAGE_SIZE, 'to': before - 1}  # "to" is inclusive
        tracks, _ = self._read(credentials, 'user.getRecentTracks', params, 'recenttracks')

        remotes = []
        for entry in tracks:
            remote = self._parse_track(entry)
            if remote and 0 < remote.at < before:
                remotes.append(remote)
        return remotes

    def pull_feedback(self, credentials: dict[str, Any], account: str) -> Iterator[RemoteListen]:
        """Fetch likes (no dislikes on Last.fm)."""

        page = 1
        while True:

            params = {'user': account, 'limit': PAGE_SIZE, 'page': page}
            tracks, attr = self._read(credentials, 'user.getLovedTracks', params, 'lovedtracks')

            for entry in tracks:
                remote = self._parse_track(entry)
                if remote:
                    remote.score = 1
                    yield remote

            try:
                pages = int(attr.get('totalPages') or 0)
            except (TypeError, ValueError):
                pages = 0

            if not tracks or page >= pages:
                return

            page += 1
