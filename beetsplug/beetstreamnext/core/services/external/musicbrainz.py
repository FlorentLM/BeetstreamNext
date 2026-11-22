from __future__ import annotations

from beetsplug.beetstreamnext.core.services.external import ExternalService
from beetsplug.beetstreamnext.core.services.external.session import capped_image_fetch


class MusicBrainz(ExternalService):
    """MusicBrainz, its Cover Art Archive, and the Wikidata entries it links to."""

    label = 'MusicBrainz'

    def query(self, mbid: str, data_type: str, cached_only: bool = False) -> dict:
        """A recording ('track'), release ('album') or artist (with its URL relations)."""

        types_mb = {'track': 'recording', 'album': 'release', 'artist': 'artist'}
        params = {'fmt': 'json'}
        if types_mb[data_type] == 'artist':
            params['inc'] = 'url-rels'

        return self.get_json(
            f'https://musicbrainz.org/ws/2/{types_mb[data_type]}/{mbid}', params=params, only_if_cached=cached_only
        )

    def get_wiki_name(self, mbid: str) -> str | None:
        """Grab an artist's exact (english) Wikipedia article title via the Wikidata link on its MusicBrainz page."""
        if not mbid:
            return None

        relations = self.query(mbid, data_type='artist').get('relations', [])
        wikidata_url = next(
            (r.get('url', {}).get('resource', '') for r in relations if r.get('type') == 'wikidata'), ''
        )
        qid = wikidata_url.rstrip('/').rsplit('/', 1)[-1]
        if not qid:
            return None

        entity = self.get_json(f'https://www.wikidata.org/wiki/Special:EntityData/{qid}.json')
        return entity.get('entities', {}).get(qid, {}).get('sitelinks', {}).get('enwiki', {}).get('title') or None

    def cover_art(self, mbid: str) -> bytes:
        """A release's front cover (size-capped, cached). Returns b'' if not found to avoid retries."""
        if not mbid:
            return b''
        return capped_image_fetch(f'https://coverartarchive.org/release/{mbid}/front')


musicbrainz_service = MusicBrainz()
