from __future__ import annotations

import urllib.parse
from typing import Optional, Dict
import requests

from beetsplug.beetstreamnext.constants import USER_AGENT
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.external.session import http_session


_DEEZER_PLACEHOLDER_HASHES = frozenset({
    'd41d8cd98f00b204e9800998ecf8427e',
})


def _is_deezer_placeholder(artist_data: Dict) -> bool:
    url = artist_data.get('picture_small', '')

    if '//56x56' in url or '//250x250' in url:
        return True

    for h in _DEEZER_PLACEHOLDER_HASHES:
        if h in url:
            return True
    return not bool(url)


def query_deezer(artist: Optional[str] = None, album: Optional[str] = None) -> dict:

    if not artist and not album:
        return {}

    artist = str(artist) if artist else ''
    album = str(album) if album else ''
    artist_quot = urllib.parse.quote_plus(artist)
    album_quot = urllib.parse.quote_plus(album)

    base_search = 'https://api.deezer.com/search/'

    if artist_quot and album_quot:
        search_endpoint = base_search + f'?q=artist:"{artist_quot}" album:"{album_quot}"'
    elif artist_quot:
        search_endpoint = base_search + f'artist?q={artist_quot}'
    elif album_quot:
        search_endpoint = base_search + f'album?q={album_quot}'

    search_endpoint += '&limit=5&index=0'

    headers = {'User-Agent': USER_AGENT}

    try:
        response = http_session().get(search_endpoint, headers=headers, timeout=8)
        if response.from_cache:
            bsn_logger.debug(f"Cache hit for Deezer: {artist}")

        if response.ok:
            candidates = response.json().get('data', [])

            if candidates and artist:
                # Prefer exact name matches
                exact_matches = [c for c in candidates if c.get('name', '').lower() == artist.lower()]
                pool = exact_matches if exact_matches else candidates
                if len(pool) == 1:
                    return pool[0]

                # Prefer candidates with a real image
                with_image = [c for c in pool if not _is_deezer_placeholder(c)]
                pool = with_image if with_image else pool
                if len(pool) == 1:
                    return pool[0]

                # Last resort take the one with highest nb_fan
                return max(pool, key=lambda c: c.get('nb_fan', 0))

    except requests.exceptions.RequestException:
        pass

    return {}
