from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Dict, Tuple

import flask

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.utils.concurrency import BuildOnce
from beetsplug.beetstreamnext.utils.db import get_beets_schema, library_cache
from beetsplug.beetstreamnext.utils.system import path_hash
from beetsplug.beetstreamnext.utils.text import split_beets_multi, validate_mbid, sha1_hex


class Index(ABC):
    """
    Base for Library indexes.
    """

    _builder: BuildOnce

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._builder = BuildOnce()

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @classmethod
    @abstractmethod
    def _cache_name(cls) -> str:
        """Name of the library_cache bucket."""

    @classmethod
    @abstractmethod
    def _build(cls) -> Index:
        """(Re)build new index from the library."""

    @classmethod
    def get(cls):
        return cls._builder.get(library_cache(cls._cache_name()), 'index', cls._build)


class ArtistIndex(Index):
    """
    Index of artist credits from the library for quick per-name metadata lookups.
    """

    def __init__(self, lib):
        super().__init__()

        cols = get_beets_schema('items')

        def column(*names: str) -> str:
            return next((n for n in names if n in cols), 'NULL')

        # name -> (mbid, sort name) of the first album/song credited
        self._album_artists: Dict[str, Tuple[str, str]] = {}
        self._song_artists: Dict[str, Tuple[str, str]] = {}

        multi_artists = set()
        composers = set()
        lyricists = set()

        with lib.transaction() as tx:
            album_rows = tx.query(
                """
                SELECT albumartist, mb_albumartistid, albumartist_sort
                FROM albums
                ORDER BY id
                """)

            song_rows = tx.query(
                f"""
                SELECT artist, mb_artistid, artist_sort, {column('artists')},
                       {column('composers', 'composer')}, {column('lyricists', 'lyricist')}
                FROM items
                ORDER BY id
                """)  # nosec B608

        for name, mbid, sort_name in album_rows:
            self._album_artists.setdefault(name, (mbid, sort_name))

        for name, mbid, sort_name, artists, comp, lyr in song_rows:
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
        """Every name in a multi-value field."""
        return {name for value in values for name in split_beets_multi(value)}

    def metadata(self, name: str) -> dict:
        with self._lock:
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

        # Secondary roles
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

    @classmethod
    def _cache_name(cls) -> str:
        return 'artist_index'

    @classmethod
    def _build(cls) -> ArtistIndex:
        return cls(flask.g.lib)


class SongIndex(Index):
    """
    Index of song MBIDs and path hashes mapped to beets IDs.
    """

    def __init__(self, lib, music_root):
        super().__init__()

        # Lowest beets ID wins when songs have the same MBID
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
            rows = tx.query(
                """
                SELECT id, path, mb_releasetrackid, mb_trackid
                FROM items
                ORDER BY id DESC
                """)

        for beets_id, path, release_track_id, track_id in rows:
            self.ids.add(beets_id)

            if release_track_id or track_id:
                for mbid in filter(None, (release_track_id, track_id)):
                    self.by_mbid[mbid] = beets_id
            else:
                self.by_hash[path_hash(path, music_root)] = beets_id

    @classmethod
    def _cache_name(cls) -> str:
        return f"song_index:{app.config['music_root']}"

    @classmethod
    def _build(cls) -> SongIndex:
        return cls(flask.g.lib, app.config['music_root'])

