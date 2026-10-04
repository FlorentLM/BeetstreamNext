from __future__ import annotations

import requests

from beetsplug.beetstreamnext.constants import USER_AGENT
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.external.session import capped_image_fetch, http_session


def query_musicbrainz(mbid: str, data_type: str) -> dict:

    types_mb = {'track': 'recording', 'album': 'release', 'artist': 'artist'}
    endpoint = f'https://musicbrainz.org/ws/2/{types_mb[data_type]}/{mbid}'

    headers = {'User-Agent': USER_AGENT}
    params = {'fmt': 'json'}

    if types_mb[data_type] == 'artist':
        params['inc'] = 'url-rels'

    try:
        response = http_session().get(endpoint, headers=headers, params=params, timeout=8)
        if response.from_cache:
            bsn_logger.debug(f"Cache hit for MusicBrainz: {mbid}")
        return response.json() if response.ok else {}

    except requests.exceptions.RequestException:
        return {}


def query_wikidata_title(mbid: str) -> str | None:
    """
    Resolve an artist's exact (english) Wikipedia article title via MusicBrainz's Wikidata
    """
    if not mbid:
        return None

    relations = query_musicbrainz(mbid, data_type='artist').get('relations', [])
    wikidata_url = next(
        (r.get('url', {}).get('resource', '') for r in relations if r.get('type') == 'wikidata'), ''
    )
    qid = wikidata_url.rstrip('/').rsplit('/', 1)[-1]
    if not qid:
        return None

    try:
        response = http_session().get(
            f'https://www.wikidata.org/wiki/Special:EntityData/{qid}.json',
            headers={'User-Agent': USER_AGENT}, timeout=8
        )
        if not response.ok:
            return None
        entity = response.json().get('entities', {}).get(qid, {})
        return entity.get('sitelinks', {}).get('enwiki', {}).get('title') or None

    except requests.exceptions.RequestException:
        return None


def query_coverartarchive(mbid: str) -> bytes:
    """Fetch image from CAA (size-capped) and cache the bytes. Returns b'' if not found to avoid retries."""
    if not mbid:
        return b''
    return capped_image_fetch(f'https://coverartarchive.org/release/{mbid}/front')
