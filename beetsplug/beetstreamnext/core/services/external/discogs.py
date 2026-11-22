from __future__ import annotations

from typing import Any

from beetsplug.beetstreamnext.core.services.external import ExternalService


class Discogs(ExternalService):
    """Unauthenticated, so 25 req/min maximum."""

    label = 'Discogs'

    def release(self, release_id: Any, cached_only: bool = False) -> dict:
        if not release_id:
            return {}
        return self.get_json(f'https://api.discogs.com/releases/{release_id}', only_if_cached=cached_only)


discogs_service = Discogs()
