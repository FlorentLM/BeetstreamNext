from __future__ import annotations

from beetsplug.beetstreamnext.constants import PODCASTINDEX
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.settings import settings_store


def test_podcastindex_connection() -> tuple[bool, str]:
    """Check that the configured Podcast Index credentials are valid. Returns (ok, message)."""

    if not PODCASTINDEX:
        return False, "The 'python-podcastindex' package is not installed on the server."

    api_key = settings_store.get('podcastindex_api_key')
    api_secret = settings_store.get('podcastindex_api_secret')
    if not api_key or not api_secret:
        return False, 'No Podcast Index API key/secret configured.'

    import podcastindex

    try:
        index = podcastindex.init({'api_key': api_key, 'api_secret': api_secret})
        result = index.search('test')
    except Exception as e:
        return False, f'Could not reach Podcast Index: {e}'

    if not isinstance(result, dict) or str(result.get('status')).lower() != 'true':
        return False, 'Podcast Index rejected the API key/secret.'

    return True, 'Connected to Podcast Index successfully.'


def query_podcastindex(q: str, limit: int = 15) -> list:

    if not PODCASTINDEX:
        return []

    api_key = settings_store.get('podcastindex_api_key')
    api_secret = settings_store.get('podcastindex_api_secret')
    if not api_key or not api_secret:
        return []

    import podcastindex

    try:
        index = podcastindex.init({'api_key': api_key, 'api_secret': api_secret})
        result = index.search(q)
    except Exception as e:
        bsn_logger.error(f'Podcast Index query failed: {e}')
        return []

    feeds = (result or {}).get('feeds') or []

    return [{
        'title': f.get('title') or '',
        'url': f.get('url') or '',
        'description': f.get('description') or '',
        'author': f.get('author') or f.get('ownerName') or '',
        'image': f.get('image') or f.get('artwork') or '',
        'episode_count': f.get('episodeCount') or 0,
    } for f in feeds[:limit]]
