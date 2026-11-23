from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from collections.abc import Iterator, Sequence

from beetsplug.beetstreamnext.constants import SCROBBLE_BATCH_SIZE, SCROBBLE_HTTP_TIMEOUT_SEC
from beetsplug.beetstreamnext.core.library.resolve import standardise_datadict
from beetsplug.beetstreamnext.core.services.external import ExternalService
from beetsplug.beetstreamnext.utils.text import split_beets_multi, validate_mbid


class ScrobbleStatus(StrEnum):
    OK = 'ok'
    RETRY = 'retry'
    INVALID_AUTH = 'invalid_auth'   # User must reconnect
    DROP = 'drop'                   # Rejected
    SKIP = 'skip'                   # Service cant accept anything for this track


class ScrobbleError(Exception):
    """The message is safe to show to the user."""


@dataclass
class ScrobbleTrack:
    artist: str
    title: str
    album: str = ''
    duration_s: int = 0
    track_number: int = 0
    recording_mbid: str = ''
    release_mbid: str = ''
    artist_mbids: list[str] = field(default_factory=list)
    played_at: int = 0         # Unix seconds (0 = now)

    @classmethod
    def from_item(cls, item: Any, played_at: float = 0) -> ScrobbleTrack:
        data = standardise_datadict(item)
        artist_mbids = split_beets_multi(data.get('mb_artistids') or data.get('mb_artistid') or '')

        return cls(
            artist=str(data.get('artist') or '').strip(),
            title=str(data.get('title') or '').strip(),
            album=str(data.get('album') or '').strip(),
            duration_s=int(float(data.get('length') or 0)),
            track_number=int(data.get('track') or 0),
            recording_mbid=validate_mbid(data.get('mb_trackid')),    # beets' mb_trackid is the MusicBrainz *recording*
            release_mbid=validate_mbid(data.get('mb_albumid')),
            artist_mbids=[mbid for mbid in map(validate_mbid, artist_mbids) if mbid],
            played_at=int(played_at),
        )


@dataclass
class RemoteListen:
    """A listen or a like/dislike pulled from a service."""
    artist: str
    title: str
    album: str = ''
    recording_mbid: str = ''
    at: int = 0     # Unix seconds
    score: int = 0  # 1 (like), -1 (dislike), 0 (neither)


class ScrobblerBase(ExternalService, ABC):
    """
    An external listening history service.
    `credentials` is the decrypted dict stored for the user, specific to the service.
    """

    service: str = ''
    timeout: int = SCROBBLE_HTTP_TIMEOUT_SEC
    self_hosted = True
    credentials_url: str = ''
    default_url: str = ''
    batch_size: int = SCROBBLE_BATCH_SIZE   # Max listens per request
    auth_kind: str = 'token'                # 'token' or 'webauth'

    @property
    def capabilities(self) -> frozenset[str]:
        """The optional methods this service implements."""
        return frozenset(name for name in ('pull_listens', 'pull_feedback', 'push_feedback')
                         if getattr(type(self), name) is not getattr(ScrobblerBase, name))

    def available(self) -> bool:
        """Server has everything the service needs (API keys...)?"""
        return True

    def auth_page(self, pending_token: str) -> str:
        """Web auth page (where user approves the token)."""
        raise NotImplementedError

    def start_auth(self) -> tuple[str, str]:
        """Web auth step 1. Returns (pending token to keep in session, URL user should go to)."""
        raise NotImplementedError

    def finish_auth(self, pending_token: str) -> tuple[dict[str, Any], str]:
        """Web auth step 2 (after user approved). Returns (credentials, remote account name)."""
        raise NotImplementedError

    @abstractmethod
    def verify(self, credentials: dict[str, Any]) -> str:
        """Returns the remote account name. Raises ScrobbleError if the credentials don't work."""

    @abstractmethod
    def now_playing(self, credentials: dict[str, Any], track: ScrobbleTrack) -> ScrobbleStatus:
        ...

    @abstractmethod
    def submit(self, credentials: dict[str, Any], tracks: Sequence[ScrobbleTrack]) -> ScrobbleStatus:
        ...

    def pull_listens(self, credentials: dict[str, Any], account: str, before: int) -> list[RemoteListen]:
        raise NotImplementedError

    def pull_feedback(self, credentials: dict[str, Any], account: str) -> Iterator[RemoteListen]:
        """Fetch likes and dislikes (score 1 or -1)."""
        raise NotImplementedError

    def push_feedback(self, credentials: dict[str, Any], track: ScrobbleTrack, score: int) -> ScrobbleStatus:
        """Send a like, dislike or neither (score 1, -1 or 0)."""
        raise NotImplementedError
