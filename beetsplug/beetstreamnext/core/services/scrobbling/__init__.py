from __future__ import annotations

from beetsplug.beetstreamnext.core.services.scrobbling.base import ScrobblerBase
from beetsplug.beetstreamnext.core.services.scrobbling.lastfm import LastFmScrobbler
from beetsplug.beetstreamnext.core.services.scrobbling.listenbrainz import ListenBrainzScrobbler

SCROBBLERS = {s.service: s for s in (
    ListenBrainzScrobbler(),
    LastFmScrobbler('lastfm', 'Last.fm', 'https://ws.audioscrobbler.com/2.0/', 'https://www.last.fm/api/auth/'),
    LastFmScrobbler('librefm', 'Libre.fm', 'https://libre.fm/2.0/', 'https://libre.fm/api/auth/'),
)}
