from __future__ import annotations

from abc import ABC, abstractmethod
from typing import NamedTuple

import flask

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import SOURCE_TRACK_ID_FIELDS, SOURCE_ALBUM_ID_FIELDS
from beetsplug.beetstreamnext.utils.concurrency import BuildOnce
from beetsplug.beetstreamnext.utils.db import library_cache
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.utils.system import path_hash
from beetsplug.beetstreamnext.utils.text import split_beets_multi, validate_mbid, sha1_hex


##

class Artist(NamedTuple):
    id: str
    name: str
    mbid: str
    sort_name: str
    joint: bool
    members: tuple[str, ...]        # if joint credit
    album_ids: tuple[int, ...]      # Beets IDs of this artist's albums
    keys: tuple[str, ...]           # IDs an image can be stored under (best first)

    @classmethod
    def bare(cls, name: str) -> Artist:
        """Artist the library has nothing about but a name."""
        artist_id = IDs.encode_artist(name, is_mbid=False)
        return cls(artist_id, name, '', name, False, (), (), (artist_id,))

    @property
    def candidates(self) -> tuple[str, ...]:
        """Names to look up an image."""
        return (self.name, *(m for m in self.members if m != self.name))

    def link_kind(self, key: str) -> str:
        """How an image stored with `key` is linked to this artist: 'mbid', 'mbids' (joint credit) or 'name'."""
        kind = IDs.decode_artist(key)[1]
        if kind == 'hash':
            return 'mbids' if len(self.keys) > 1 and key == self.keys[0] else 'name'
        return kind


##

class Index(ABC):
    """
    Base for Library indexes.
    """

    _builder: BuildOnce

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._builder = BuildOnce()

    @classmethod
    @abstractmethod
    def _build(cls) -> Index:
        """(Re)build new index from the library."""

    @classmethod
    def get(cls):
        return cls._builder.get(library_cache(cls.__name__), 'index', cls._build)


class SongIndex(Index):
    """
    Beets IDs of songs by MBID (or Spotify/Deezer ID), path hash and (artist, title), and Subsonic ID and album.
    """

    def __init__(self, lib, music_root):
        # Lowest beets ID wins when songs have the same MBID
        self.by_mbid: dict[str, int] = {}
        self.by_hash: dict[str, int] = {}
        self.subsonic_ids: dict[int, str] = {}
        self.album_of: dict[int, int] = {}      # beets song ID -> beets album ID
        self._by_name: dict[tuple[str, str], list[int]] = {}   # (artist, title) casefolded -> beets song IDs

        artists_col = 'artists' if 'artists' in library_cache.schema('items') else 'NULL'

        with lib.transaction() as tx:
            rows = tx.query(
                f"""
                SELECT id, path, mb_releasetrackid, mb_trackid, album_id, title, artist, albumartist, {artists_col}
                FROM items
                ORDER BY id DESC
                """)  # nosec B608  # noqa: S608 vars are constants

        for beets_id, path, release_track_id, track_id, album_id, title, artist, albumartist, artists in rows:
            digest = path_hash(path, music_root)
            self.by_hash[digest] = beets_id
            self.subsonic_ids[beets_id] = IDs.song_id(release_track_id or track_id, digest, beets_id)
            if album_id is not None:
                self.album_of[beets_id] = album_id

            for mbid in filter(None, (str(release_track_id or '').strip(), str(track_id or '').strip())):
                self.by_mbid[mbid] = beets_id

            if title:
                for name in {artist, albumartist, *split_beets_multi(artists or '')} - {None, ''}:
                    self._by_name.setdefault((name.casefold(), title.casefold()), []).append(beets_id)

        # Fallback: IDs recorded by non-MB source plugin (Spotify, Deezer...)
        with lib.transaction() as tx:
            source_rows = tx.query(
                f"""
                SELECT entity_id, value
                FROM item_attributes
                WHERE key IN ({','.join('?' * len(SOURCE_TRACK_ID_FIELDS))})
                ORDER BY entity_id DESC
                """, SOURCE_TRACK_ID_FIELDS)  # nosec B608  # noqa: S608 vars are constants

        primary = set(self.by_mbid)
        for beets_id, value in source_rows:
            if str(value) not in primary:
                self.by_mbid[str(value)] = beets_id

    def named(self, artist: str, title: str) -> list[int]:
        """Beets IDs of songs with this title and this artist (case ignored)."""
        return self._by_name.get(((artist or '').casefold(), (title or '').casefold()), [])[::-1]

    def id_of(self, item) -> str:
        """Subsonic ID of a beets item (Item, row or dict)."""
        return self.subsonic_ids.get(item['id']) or IDs.encode_song(
            {key: item[key] for key in ('id', 'path', 'mb_releasetrackid', 'mb_trackid')})

    @classmethod
    def _build(cls) -> SongIndex:
        return cls(flask.g.lib, app.config['music_root'])


class ArtistIndex(Index):
    """
    Every artist of the library by ID.
    """

    def __init__(self, lib):
        album_cols = library_cache.schema('albums')
        item_cols = library_cache.schema('items')

        def column(cols: list[str], *names: str) -> str:
            return next((n for n in names if n in cols), 'NULL')

        with lib.transaction() as tx:
            album_rows = tx.query(
                f"""
                SELECT id, albumartist, mb_albumartistid, albumartist_sort,
                       {column(album_cols, 'albumartists')}, {column(album_cols, 'mb_albumartistids')}
                FROM albums
                WHERE albumartist IS NOT NULL AND albumartist != ''
                ORDER BY id
                """)  # nosec B608  # noqa: S608 vars are constants

            # SELECT DISTINCT -> few rows to go through in Python
            # Ttrack artist with the same name as the album artist: MBID counts
            item_rows = tx.query(
                f"""
                SELECT DISTINCT artist,
                       COALESCE(NULLIF(mb_artistid, ''), CASE WHEN artist = albumartist THEN mb_albumartistid END),
                       artist_sort,
                       {column(item_cols, 'artists')}, {column(item_cols, 'mb_artistids')},
                       {column(item_cols, 'composers', 'composer')}, {column(item_cols, 'lyricists', 'lyricist')}
                FROM items
                """)  # nosec B608  # noqa: S608 vars are constants

        credit_rows: dict[tuple, str] = {}      # (artist, mbid, artists, mbids) -> first sort name met
        composers, lyricists = set(), set()

        for name, mbid, sort_name, names_raw, mbids_raw, comp, lyr in item_rows:
            credit_rows.setdefault((name, mbid, names_raw, mbids_raw), sort_name)
            composers.add(comp)
            lyricists.add(lyr)

        self._composers = {name for value in composers for name in split_beets_multi(value)}
        self._lyricists = {name for value in lyricists for name in split_beets_multi(value)}

        self._mbids: dict[str, dict[str, None]] = {}   # name -> MBIDs
        untagged: set[str] = set()                     # Names with no MBID

        def record(pairs: list[tuple[str, str]]) -> None:
            for name, mbid in pairs:
                if mbid:
                    self._mbids.setdefault(name, {})[mbid] = None
                else:
                    untagged.add(name)

        # Album artists: rows by name + the first sort name
        album_groups: dict[str, list] = {}
        joint_albums: dict[str, tuple] = {}    # name -> first joint row

        for row in album_rows:
            _id, name, mbid, sort_name, names_raw, mbids_raw = row
            album_groups.setdefault(name, []).append(row)

            joint = IDs.is_joint_credit(names_raw, name)
            if joint:
                joint_albums.setdefault(name, row)
            record(IDs.credit_pairs(name, mbid, names_raw, mbids_raw, joint=joint))

        # Track artists
        self._song_sort: dict[str, str] = {}
        joint_songs: dict[str, tuple] = {}
        member_names: dict[str, None] = {}

        for (name, mbid, names_raw, mbids_raw), sort_name in credit_rows.items():
            if not name:
                continue
            self._song_sort.setdefault(name, sort_name or '')

            joint = IDs.is_joint_credit(names_raw, name)
            pairs = IDs.credit_pairs(name, mbid, names_raw, mbids_raw, joint=joint)
            record(pairs)
            if joint:
                joint_songs.setdefault(name, (names_raw, mbids_raw))
                member_names.update(dict.fromkeys(n for n, _ in pairs))

        for row in joint_albums.values():
            member_names.update(dict.fromkeys(split_beets_multi(row[4] or '')))

        # Entries
        self.by_id: dict[str, Artist] = {}
        self._by_name: dict[str, list[Artist]] = {}
        self.source_ids: dict[str, Artist] = {}       # non-MusicBrainz value found in mb_albumartistid -> artist
        self.by_album: dict[int, Artist] = {}         # Beets album ID -> Artist

        def add(name: str, mbid: str, rows: list, *, joint: tuple[str | None, str | None] | None = None) -> Artist:
            if joint:
                names_raw, mbids_raw = joint
                keys = IDs.joint_credit_keys(name, names_raw, mbids_raw)
                members = tuple(split_beets_multi(names_raw or ''))
            else:
                keys = [IDs.encode_artist(name, is_mbid=False)] if not mbid or not self.shared(name) else []
                if mbid:
                    keys.insert(0, IDs.encode_artist(mbid, is_mbid=True))
                members = ()

            sort_name = next((r[3] for r in rows if r[3]), '') or self._song_sort.get(name) or name

            artist = Artist(
                id=IDs.artist_id(name, mbid, joint=bool(joint)), name=name, mbid=mbid, sort_name=sort_name,
                joint=bool(joint), members=members, album_ids=tuple(r[0] for r in rows), keys=tuple(keys),
            )

            self.by_id.setdefault(artist.id, artist)    # One MBID but two spellings? First one takes the ID
            self._by_name.setdefault(name, []).append(artist)

            for r in rows:
                self.by_album[r[0]] = artist
                raw = str(r[2] or '').strip()
                if raw and not validate_mbid(raw):
                    self.source_ids.setdefault(raw, artist)

            return artist

        def add_solo(name: str, rows: list) -> None:
            mbids = list(self._mbids.get(name, ()))

            if len(mbids) <= 1:
                add(name, mbids[0] if mbids else '', rows)
                return

            # Shared name? One artist per MBID, + one more for a no-MBID
            by_mbid: dict[str, list] = {mbid: [] for mbid in mbids}
            for r in rows:
                by_mbid.setdefault(validate_mbid(r[2]), []).append(r)

            for mbid, own_rows in by_mbid.items():
                add(name, mbid, own_rows)

            if '' not in by_mbid and name in untagged:
                add(name, '', [])

        for name in sorted(album_groups):
            if name in joint_albums:
                row = joint_albums[name]
                add(name, '', album_groups[name], joint=(row[4], row[5]))
            else:
                add_solo(name, album_groups[name])

        self.album_artists: list[Artist] = [a for artists in self._by_name.values() for a in artists if a.album_ids]

        # Artists only credited on tracks (or as a member of a credit)
        for name in (*self._song_sort, *member_names):
            if name in self._by_name:
                continue
            if name in joint_songs:
                add(name, '', [], joint=joint_songs[name])
            else:
                add_solo(name, [])

        self._by_folded: dict[str, list[Artist]] = {}
        for name, artists in self._by_name.items():
            self._by_folded.setdefault(name.casefold(), []).extend(artists)

        self._by_name_hash: dict[str, str] = {sha1_hex(name)[:16]: name for name in album_groups}
        self._joint_names: set[str] = {*joint_albums, *joint_songs}
        self._album_artist_names: set[str] = set(album_groups)

    def shared(self, name: str) -> bool:
        """Whether the library knows this name under more than one MBID."""
        return len(self._mbids.get(name, ())) > 1

    def mbid_of(self, name: str) -> str:
        """MBID for a name, when unambiguous."""
        mbids = self._mbids.get(name)
        return next(iter(mbids)) if mbids and len(mbids) == 1 else ''

    def id_of(self, data: dict) -> str:
        """
        ID of the main artist of a song or album.
        An untagged one joins the only tagged artist of that name, if there is exactly one.
        """
        name, mbid, multi = IDs.main_artist_of(data)
        joint = name in self._joint_names or IDs.is_joint_credit(multi, name)
        if not joint and not mbid:
            mbid = self.mbid_of(name)
        return IDs.artist_id(name, mbid, joint=joint)

    def named(self, name: str) -> tuple[Artist, ...]:
        """Every artist with this name (exact spelling first, then ignoring case)."""
        return tuple(self._by_name.get(name) or self._by_folded.get((name or '').casefold()) or ())

    def find(self, name: str) -> Artist | None:
        """Artist corresponding to a bare name (only one with that name, or the name-only one if name is shared)."""
        artists = self.named(name)
        if len(artists) == 1:
            return artists[0]
        return next((a for a in artists if not a.mbid and not a.joint), None)

    def lookup(self, id_or_name: str) -> Artist | None:

        artist = self.by_id.get(id_or_name)
        if artist is not None:
            return artist

        value, kind = IDs.decode_artist(id_or_name)
        if kind == 'hash':
            return self.find(self._by_name_hash.get(value, ''))
        if kind == 'name':
            return self.find(value)
        if kind == 'mbid':
            return None
        return self.find(id_or_name)

    def roles(self, name: str) -> list[str]:
        """What a name is credited as in the library."""
        if not name:
            return []

        roles = []
        if name in self._album_artist_names:
            roles.append('albumartist')
        if name in self._song_sort or (not roles and name in self._by_name):
            roles.append('artist')
        if name in self._composers:
            roles.append('composer')
        if name in self._lyricists:
            roles.append('lyricist')

        return roles or ['artist']

    @classmethod
    def _build(cls) -> ArtistIndex:
        return cls(flask.g.lib)


class AlbumIndex(Index):
    """
    Beets IDs of albums by MBID (or Spotify/Deezer ID) and hash, and Subsonic ID.
    """

    def __init__(self, lib):

        self.by_mbid: dict[str, int] = {}
        self.by_hash: dict[str, int] = {}
        self.subsonic_ids: dict[int, str] = {}

        with lib.transaction() as tx:
            rows = tx.query(
                """
                SELECT id, mb_albumid, albumartist, album
                FROM albums
                ORDER BY id DESC
                """)

            source_rows = tx.query(
                f"""
                SELECT DISTINCT i.album_id, a.value
                FROM item_attributes a JOIN items i ON i.id = a.entity_id
                WHERE a.key IN ({','.join('?' * len(SOURCE_ALBUM_ID_FIELDS))}) AND i.album_id IS NOT NULL
                ORDER BY i.album_id DESC
                """, SOURCE_ALBUM_ID_FIELDS)  # nosec B608  # noqa: S608 vars are constants

        by_kind = {'mbid': self.by_mbid, 'hash': self.by_hash}
        for beets_id, mbid, albumartist, album in rows:
            album_id = IDs.encode_album(beets_id, mbid, albumartist, album)
            self.subsonic_ids[beets_id] = album_id
            value, kind = IDs.decode_album(album_id)
            if kind in by_kind:
                by_kind[kind][value] = beets_id

        primary = set(self.by_mbid)
        for beets_id, value in source_rows:
            if str(value) not in primary:
                self.by_mbid[str(value)] = beets_id

    def beets_id(self, subsonic_id: str) -> int | None:
        value, kind = IDs.decode_album(subsonic_id)
        if kind == 'legacy_int':
            return value if value in self.subsonic_ids else None
        return {'mbid': self.by_mbid, 'hash': self.by_hash}.get(kind, {}).get(value)

    @classmethod
    def _build(cls) -> AlbumIndex:
        return cls(flask.g.lib)
