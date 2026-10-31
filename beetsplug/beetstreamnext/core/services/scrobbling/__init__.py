from __future__ import annotations

from beetsplug.beetstreamnext.core.services.scrobbling.base import ScrobblerBase
from beetsplug.beetstreamnext.core.services.scrobbling.listenbrainz import ListenBrainzScrobbler

SCROBBLERS = {s.service: s for s in (ListenBrainzScrobbler(),)}
