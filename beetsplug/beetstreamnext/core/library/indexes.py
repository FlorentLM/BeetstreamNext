from __future__ import annotations

from typing import Dict, Tuple

from beetsplug.beetstreamnext.utils.db import get_beets_schema
from beetsplug.beetstreamnext.utils.system import path_hash
from beetsplug.beetstreamnext.utils.text import split_beets_multi, validate_mbid


class _ArtistIndex:
    """
    Artist credits of the whole beets library, read in one pass.

    Answers per-name metadata lookups without querying the db: no index serves a name inside
    a multi-value field, so each role check would be a full scan of the items table.
    """

    def __init__(self, lib):
        cols = get_beets_schema('items')

        def column(*names: str) -> str:
            return next((n for n in names if n in cols), 'NULL')

        # name -> (mbid, sort name) of the first album/song credited to it
        self._album_artists: Dict[str, Tuple[str, str]] = {}
        self._song_artists: Dict[str, Tuple[str, str]] = {}
        multi_artists, composers, lyricists = set(), set(), set()

        with lib.transaction() as tx:
            for name, mbid, sort_name in tx.query(
                    """
                    SELECT albumartist, mb_albumartistid, albumartist_sort
                    FROM albums
                    ORDER BY id
                    """):
                self._album_artists.setdefault(name, (mbid, sort_name))

            for name, mbid, sort_name, artists, comp, lyr in tx.query(
                    f"""
                    SELECT artist, mb_artistid, artist_sort, {column('artists')},
                           {column('composers', 'composer')}, {column('lyricists', 'lyricist')}
                    FROM items
                    ORDER BY id
                    """):  # nosec B608
                self._song_artists.setdefault(name, (mbid, sort_name))
                multi_artists.add(artists)
                composers.add(comp)
                lyricists.add(lyr)

        self._multi_artists = self._names(multi_artists)
        self._composers = self._names(composers)
        self._lyricists = self._names(lyricists)

        self._metadata: Dict[str, dict] = {}

    @staticmethod
    def _names(values: set) -> set:
        """Every name in the distinct values of a multi-value field."""
        return {name for value in values for name in split_beets_multi(value)}

    def metadata(self, name: str) -> dict:
        result = self._metadata.get(name)
        if result is None:
            result = self._metadata[name] = self._lookup(name)
        return result

    def _lookup(self, name: str) -> dict:
        mbid = ''
        sort_name = ''
        roles = []

        album_row = self._album_artists.get(name)
        if album_row is not None:
            roles.append('albumartist')
            if album_row[0]:
                mbid = validate_mbid(album_row[0])
            if album_row[1]:
                sort_name = album_row[1]

        song_row = self._song_artists.get(name)
        if song_row is not None:
            roles.append('artist')
            if not mbid and song_row[0]:
                mbid = validate_mbid(song_row[0])
            if not sort_name and song_row[1]:
                sort_name = song_row[1]

        # Check for secondary roles
        if not roles and name in self._multi_artists:
            roles.append('artist')
        if name in self._composers:
            roles.append('composer')
        if name in self._lyricists:
            roles.append('lyricist')

        return {
            'mbid': mbid,
            'sort_name': sort_name or name,
            'roles': roles if roles else ['artist']
        }


class _SongIndex:
    """
    Song IDs of the whole beets library, read in one pass.

    MBIDs aren't indexed in the items table and hash IDs need every candidate's path
    hashed, so resolving either per request is a full scan of the items table.
    """

    def __init__(self, lib, music_root):
        # Several songs can share one ID: the lowest beets id answers for it
        self.by_mbid: Dict[str, int] = {}
        self.by_hash: Dict[str, int] = {}
        self.ids: set = set()

        with lib.transaction() as tx:
            for beets_id, path, release_track_id, track_id in tx.query(
                    """
                    SELECT id, path, mb_releasetrackid, mb_trackid
                    FROM items
                    ORDER BY id DESC
                    """):
                self.ids.add(beets_id)
                if release_track_id or track_id:
                    for mbid in filter(None, (release_track_id, track_id)):
                        self.by_mbid[mbid] = beets_id
                else:
                    self.by_hash[path_hash(path, music_root)] = beets_id
