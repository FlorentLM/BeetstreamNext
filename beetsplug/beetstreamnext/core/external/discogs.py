from __future__ import annotations

from typing import Any
import requests

from beetsplug.beetstreamnext.constants import USER_AGENT
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.core.external.session import http_session


def query_discogs(release_id: Any) -> dict:
    """
    Fetch a Discogs release.
    Unauthenticated version so 25 req/min, maximum.
    """
    if not release_id:
        return {}

    endpoint = f'https://api.discogs.com/releases/{release_id}'
    headers = {'User-Agent': USER_AGENT}

    try:
        response = http_session().get(endpoint, headers=headers, timeout=8)
        if response.from_cache:
            bsn_logger.debug(f"Cache hit for Discogs: {release_id}")
        return response.json() if response.ok else {}

    except requests.exceptions.RequestException:
        return {}
