from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Dict, List, Sequence

from beetsplug.beetstreamnext.constants import SCROBBLE_HTTP_TIMEOUT_SEC
from beetsplug.beetstreamnext.core.library.resolve import standardise_datadict
from beetsplug.beetstreamnext.core.services.external import ExternalService
from beetsplug.beetstreamnext.utils.text import split_beets_multi, validate_mbid


class ScrobbleStatus(StrEnum):
    OK = 'ok'
    RETRY = 'retry'
    INVALID_AUTH = 'invalid_auth'   # User must reconnect
    DROP = 'drop'                   # Rejected


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
    artist_mbids: List[str] = field(default_factory=list)
    played_at: int = 0         # Unix seconds (0 = now)

    @classmethod
    def from_item(cls, item: Any, played_at: float = 0) -> 'ScrobbleTrack':
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


class ScrobblerBase(ExternalService, ABC):
    """
    An external listening history service.
    `credentials` is the decrypted dict stored for the user, specific to the service.
    """

    service: str = ''
    timeout: int = SCROBBLE_HTTP_TIMEOUT_SEC
    credentials_url: str = ''
    default_url: str = ''

    @abstractmethod
    def verify(self, credentials: Dict[str, Any]) -> str:
        """Returns the remote account name. Raises ScrobbleError if the credentials don't work."""

    @abstractmethod
    def now_playing(self, credentials: Dict[str, Any], track: ScrobbleTrack) -> ScrobbleStatus:
        ...

    @abstractmethod
    def submit(self, credentials: Dict[str, Any], tracks: Sequence[ScrobbleTrack]) -> ScrobbleStatus:
        ...
