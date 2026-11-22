from __future__ import annotations

from functools import lru_cache
import asyncio
import time

from beetsplug.beetstreamnext.constants import WIKI_API, USER_AGENT
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.external import ExternalService


class Wikipedia(ExternalService):
    """English Wikipedia summaries."""

    label = 'Wikipedia'

    @staticmethod
    @lru_cache(maxsize=512)
    def _summary(title: str, _cache_ttl_hash: int) -> str | None:
        """`_cache_ttl_hash` is just to change the cache every hour."""

        if not WIKI_API:
            return None

        import wikipediaapi
        from beetsplug.beetstreamnext.utils.text import remove_accents, standard_ascii

        title = remove_accents(standard_ascii(title))
        if not title:
            return None

        async def fetch() -> str | None:
            page = wikipediaapi.AsyncWikipedia(user_agent=USER_AGENT, language='en', timeout=8).page(title)
            return await page.summary if await page.exists() else None

        try:
            return asyncio.run(fetch())
        except Exception as e:
            bsn_logger.error(f'Wikipedia query failed: {e}')
            return None

    def summary(self, title: str) -> str | None:
        return self._summary(title, round(time.time() / 3600))

wikipedia_service = Wikipedia()
