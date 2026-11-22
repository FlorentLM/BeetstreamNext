from __future__ import annotations

from beetsplug.beetstreamnext.core.services.scrobbling.base import ScrobblerBase
from beetsplug.beetstreamnext.core.services.scrobbling.lastfm import LastFMScrobbler
from beetsplug.beetstreamnext.core.services.scrobbling.listenbrainz import ListenBrainzScrobbler

SCROBBLERS = {s.service: s for s in (
    ListenBrainzScrobbler(),
    LastFMScrobbler('lastfm', 'Last.fm', 'https://ws.audioscrobbler.com/2.0/', 'https://www.last.fm/api/auth/'),
    LastFMScrobbler('librefm', 'Libre.fm', 'https://libre.fm/2.0/', 'https://libre.fm/api/auth/'),
)}
