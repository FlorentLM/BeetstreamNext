from __future__ import annotations

from functools import lru_cache
import asyncio

from beetsplug.beetstreamnext.constants import WIKI_API, USER_AGENT
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger


async def _async_wiki_search(q: str) -> str | None:

    if WIKI_API:
        import wikipediaapi
    else:
        return None

    wiki = wikipediaapi.AsyncWikipedia(user_agent=USER_AGENT, language='en', timeout=8)
    page = wiki.page(q)

    if await page.exists():
        summary = await page.summary
        return summary

    return None


@lru_cache(maxsize=512)
def query_wikipedia(q: str, _cache_ttl_hash=None) -> str | None:
    """`_cache_ttl_hash` is just to change the function signature every x seconds to inactivate the lru."""

    if not WIKI_API:
        return None

    from beetsplug.beetstreamnext.utils.text import standard_ascii
    from beetsplug.beetstreamnext.utils.text import remove_accents

    q = standard_ascii(q)
    q = remove_accents(q)
    if not q:
        return None

    try:
        return asyncio.run(_async_wiki_search(q))
    except Exception as e:
        bsn_logger.error(f'Wikipedia query failed: {e}')
        return None
