from __future__ import annotations

import re
import time
import urllib.parse
from typing import List
import requests
import asyncio

from beetsplug.beetstreamnext.constants import MAX_RADIO_HOMEPAGE_BYTES, RADIO_BROWSER, USER_AGENT
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.external.session import capped_image_fetch, fetch_public


_ICON_LINK_RE = re.compile(
    r'<link[^>]+rel=["\'](?:shortcut icon|icon|apple-touch-icon(?:-precomposed)?)["\'][^>]*>',
    re.IGNORECASE
)


_ICON_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


_ICON_SIZES_RE = re.compile(r'sizes=["\'](\d+)x\d+["\']', re.IGNORECASE)


async def _async_radio_search(query: str, limit: int) -> List[dict]:

    from radios import RadioBrowser

    async with RadioBrowser(user_agent=USER_AGENT) as rb:

        # Searching by name
        stations = await rb.search(name=query, limit=limit)
        return [{
            'name': s.name,
            'stream_url': s.url,
            'homepage_url': s.homepage,
            'favicon': s.favicon,
        } for s in stations]


def fetch_favicon(homepage_url: str) -> bytes:
    """
    Fetch a site's icon. Prefers the largest <link rel="icon"/"apple-touch-icon"> declared
    in the page's html, falling back to DuckDuckGo's favicon proxy if nothing usable was found.
    Returns b'' on failure.
    """
    domain = urllib.parse.urlparse(homepage_url).netloc
    if not domain:
        return b''

    best_url, best_size = None, 0
    try:
        resp = fetch_public(homepage_url, timeout=8)
    except ValueError:
        bsn_logger.warning(f'Refusing to fetch favicon from non-public URL: {homepage_url}')
        return b''
    except requests.exceptions.RequestException:
        resp = None

    if resp is not None:
        try:
            if resp.ok:
                page = bytearray()
                deadline = time.monotonic() + 10
                for chunk in resp.iter_content(8192):
                    page += chunk
                    if len(page) >= MAX_RADIO_HOMEPAGE_BYTES or time.monotonic() > deadline:
                        break

                html = bytes(page).decode(resp.encoding or 'utf-8', errors='replace')
                for tag in _ICON_LINK_RE.findall(html):
                    href = _ICON_HREF_RE.search(tag)
                    if not href:
                        continue
                    size_match = _ICON_SIZES_RE.search(tag)
                    size = int(size_match.group(1)) if size_match else 32
                    if size > best_size:
                        best_url, best_size = urllib.parse.urljoin(homepage_url, href.group(1)), size
        except (requests.exceptions.RequestException, LookupError):
            pass
        finally:
            resp.close()

    if best_url:
        image = capped_image_fetch(best_url)
        if image:
            return image

    return capped_image_fetch(f'https://icons.duckduckgo.com/ip3/{domain}.ico')


def query_radio_browser(q: str, limit: int = 20) -> list:

    if not RADIO_BROWSER:
        return []

    try:
        return asyncio.run(_async_radio_search(q, limit))
    except Exception as e:
        bsn_logger.error(f'Radio Browser query failed: {e}')
        return []
