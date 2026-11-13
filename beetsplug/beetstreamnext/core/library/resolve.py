from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from collections.abc import Sequence, Callable
import flask
from beets.library import LibModel, Item

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.library.indexes import Artist, ArtistIndex, SongIndex, AlbumIndex
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.library.ids import IDs, TYPES_PLAYABLE
from beetsplug.beetstreamnext.utils.system import resolve_path


def beets_abspath(item: dict | Item | Any) -> Path:
    """
    Beets sometimes stores paths relative to its 'directory' config.
    This resolves to an absolute path.
    """
    return resolve_path(item.get('path', b''), app.config['music_root'])


class AttrDict(dict):
    """
    A dict that also allows attribute-style access.
    Missing keys resolving to None.
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            return None


def standardise_datadict(obj: dict | LibModel | Item | Any) -> dict:
    """
    Standardise input (Beets Item/Album or sqlite3.Row) into a dict.
    """
    if isinstance(obj, LibModel):
        data = dict(obj)
        data['id'] = obj.id
        if hasattr(obj, 'path'):
            data['path'] = obj.path
        return data
    if isinstance(obj, dict):
        return obj
    try:
        return dict(obj)
    except (ValueError, TypeError):
        return {}


##
# Resolver: Decodes a Subsonic ID and fetches the Beets/db object it refers to


class Resolve:
    """
    Decode Subsonic IDs (minted by IDs) and fetch the Beets/db object they refer to.
    """

    _method_map: dict[str, Callable] = {}

    @classmethod
    def artist(cls, req_id: str) -> Artist | None:
        """
        Artist corresponding to any music Subsonic ID (artist, album, or song) / name.
        None if can't be resolved.

        Note: Unlike the other Resolve methods, this doesn't return a db object because beets has no
        Artist row.
        """
        entry_type, obj = cls.any(req_id)
        index = ArtistIndex.get()

        if entry_type in ('song', 'album'):
            if not obj:
                return None
            data = standardise_datadict(obj)
            return index.lookup(index.id_of(data)) or Artist.bare(IDs.main_artist_of(data)[0])

        if entry_type not in ('artist', None):
            return None                         # ID of something else (radio, playlist, podcast etc)

        artist = index.lookup(req_id)
        name, kind = IDs.decode_artist(req_id) if entry_type == 'artist' else (req_id, 'name')   # None: artist name (not ID)

        if artist is None and kind == 'name' and name and not index.named(name):
            return Artist.bare(name)        # e.g. a remixer: only known as a credit on tracks

        return artist

    @classmethod
    def album(cls, subsonic_id: str) -> LibModel | None:
        """
        Decode a Subsonic album ID (mbid, hash, or legacy row id) and fetch the beets album data.
        """
        album_id = AlbumIndex.get().beets_id(subsonic_id)
        return flask.g.lib.get_album(album_id) if album_id is not None else None

    @classmethod
    def song(cls, subsonic_id: str) -> Item | None:
        """
        Decode a Subsonic song ID (mbid, hash, or legacy row id) and fetch the beets song data.
        """
        beets_id = cls.song_ids([subsonic_id]).get(str(subsonic_id))
        return flask.g.lib.get_item(beets_id) if beets_id is not None else None

    @classmethod
    def songs(cls, subsonic_ids: Sequence[str]) -> dict[str, Item]:
        """
        Batched song resolving. Returns {id: song_object},
        """

        items: dict[int, Item | None] = {}
        result: dict[str, Item] = {}

        for sid, beets_id in cls.song_ids(subsonic_ids).items():
            if beets_id not in items:
                items[beets_id] = flask.g.lib.get_item(beets_id)

            if items[beets_id] is not None:
                result[sid] = items[beets_id]

        return result

    @classmethod
    def playlist(cls, subsonic_id: str) -> dict | None:
        playlist_id = IDs.decode_int(subsonic_id, IDs._PLY_ID_PREF)

        if playlist_id is None:
            return None

        return app.config['playlist_manager'].get(playlist_id)

    @classmethod
    def playlist_songs(cls, tracks: Sequence[dict]) -> list[Item]:
        """Tracks that don't resolve (by song ID, then by path) are omitted."""
        found = cls.songs([t['song_id'] for t in tracks if t['song_id']])

        path_ids = {t['id']: IDs.encode_song({'path': t['path']}) for t in tracks if t['path'] and t['song_id'] not in found}
        found_by_path = cls.songs(list(path_ids.values()))

        songs = [found.get(t['song_id']) or found_by_path.get(path_ids.get(t['id'])) for t in tracks]
        return [song for song in songs if song is not None]

    @classmethod
    def song_ids(cls, subsonic_ids: Sequence[str]) -> dict[str, int]:
        """
        Batched song resolving, without loading the songs. Returns {id: beets_id}.
        """

        if not subsonic_ids:
            return {}

        index = SongIndex.get()
        result: dict[str, int] = {}

        for raw in subsonic_ids:
            sid = str(raw)
            value, kind = IDs.decode_song(sid)

            if kind == 'mbid':
                beets_id = index.by_mbid.get(value)
            elif kind == 'hash':
                beets_id = index.by_hash.get(value)
            elif kind == 'legacy_int':
                beets_id = value if value in index.subsonic_ids else None
            else:
                beets_id = None

            if beets_id is not None:
                result[sid] = beets_id

        return result

    @classmethod
    def radio(cls, subsonic_id: str) -> AttrDict | None:
        """
        Decode a Subsonic radio ID and fetch the station row.
        """
        radio_id = IDs.decode_int(subsonic_id, IDs._RAD_ID_PREF)

        if radio_id is None:
            return None

        with database() as db:
            row = db.execute(
                """
                SELECT *
                FROM internet_radio_stations
                WHERE id = ?
                """, (radio_id,)
            ).fetchone()

        return AttrDict(dict(row)) if row else None

    @classmethod
    def podcast_channel(cls, subsonic_id: str) -> AttrDict | None:
        """
        Decode a Subsonic podcast channel ID and fetch its row.
        """

        channel_id = IDs.decode_int(subsonic_id, IDs._PCH_ID_PREF)
        if channel_id is None:
            return None

        with database() as db:
            row = db.execute(
                """
                SELECT * 
                FROM podcast_channels 
                WHERE id = ?
                """, (channel_id,)
            ).fetchone()

        return AttrDict(dict(row)) if row else None

    @classmethod
    def podcast_episode(cls, subsonic_id: str) -> AttrDict | None:
        """
        Decode a Subsonic podcast episode ID and fetch its row.
        """

        episode_id = IDs.decode_int(subsonic_id, IDs._PEP_ID_PREF)
        if episode_id is None:
            return None

        with database() as db:
            row = db.execute(
                """
                SELECT * 
                FROM podcast_episodes 
                WHERE id = ?
                """, (episode_id,)
            ).fetchone()

        return AttrDict(dict(row)) if row else None

    @classmethod
    def any(cls, subsonic_id: str) -> tuple[str | None, Any | None]:
        """
        Decode any Subsonic ID and fetch its data object. Returns (type, object).

        Note: 'object' is None when the ID can't be resolved, or for a type that
        isn't a plain id->db lookup (like artist or playlist).
        """
        entry_type = IDs.decode_type(subsonic_id)
        resolver = cls._method_map.get(entry_type)
        return entry_type, (resolver(subsonic_id) if resolver else None)

    @classmethod
    def multiple(cls, subsonic_ids: Sequence[str]) -> dict[str, tuple[str, Any]]:
        """
        Batched resolve for a (possibly heterogeneous) list of IDs. Returns {id: (type, object)},
        omitting anything that didn't resolve.

        TODO: Only songs are bulk-fetched, maybe this should be extended to everything?
        """
        songs = cls.songs([sid for sid in subsonic_ids if IDs.decode_type(sid) == 'song'])

        result: dict[str, tuple[str, Any]] = {}
        for sid in subsonic_ids:
            entry_type = IDs.decode_type(sid)
            obj = songs.get(sid) if entry_type == 'song' else cls.any(sid)[1]
            if obj is not None:
                result[sid] = (entry_type, obj)

        return result

    @classmethod
    def playable(cls, entry_id: str, username: str, pre_resolved: dict[str, tuple[str, Any]] | None = None) -> tuple[str, str] | None:
        """
        Resolve any playable Subsonic ID (song, radio station, or podcast episode) to (id, local path or URL).

        Args:
            - pre_resolved: An optional {id: (type, object)} map from a prior Resolve.multiple() call.
            Falls back to a single-item Resolve.any() when it's not given or doesn't have the id.
        """
        entry_type, obj = (pre_resolved or {}).get(entry_id) or Resolve.any(entry_id)

        if obj is None:
            bsn_logger.warning(f'Could not resolve {entry_id!r}, skipping.')
            return None

        if entry_type not in TYPES_PLAYABLE:
            bsn_logger.warning(f'Unsupported id type for {entry_id!r}, skipping.')
            return None

        if entry_type == 'song':
            if not obj.get('path'):
                bsn_logger.warning(f'Song {entry_id!r} has no path, skipping.')
                return None

            path = str(beets_abspath(obj))
            if not os.path.isfile(path):
                bsn_logger.warning(f'Path does not exist on disk, sending it anyway: {path!r}')

            return SongIndex.get().id_of(obj), path

        if entry_type == 'radio':

            if obj.get('owner') != username:
                bsn_logger.warning(f'Radio station {entry_id!r} belongs to someone else, skipping.')
                return None

            if not str(obj.get('stream_url') or '').lower().startswith(('http://', 'https://')):
                bsn_logger.warning(f'Radio station {entry_id!r} has no http(s) stream url, skipping.')
                return None

            return entry_id, obj['stream_url']

        if entry_type == 'podcast_episode':

            if obj.get('status') == 'completed' and obj.get('file_path'):
                return entry_id, obj['file_path']

            if str(obj.get('audio_url') or '').lower().startswith(('http://', 'https://')):
                return entry_id, obj['audio_url']

            bsn_logger.warning(f'Podcast episode {entry_id!r} has no playable source, skipping.')
            return None

    @classmethod
    def playables(cls, entry_ids: Sequence[str], username: str) -> list[tuple[str, str]]:
        """
        Batched playable resolving. Resolves a list of playable Subsonic IDs
        to (id, local path or URL) pairs, skipping unresolvable ones.
        """
        resolved = cls.multiple(entry_ids)
        return [item for entry_id in entry_ids if (item := cls.playable(entry_id, username, resolved)) is not None]

    @classmethod
    def shared_items(cls, entry_ids: Sequence[str]) -> tuple[list[Item], list[LibModel]]:
        """
        Returns a public share's entry IDs, split into song items and album objects.
        Playlists are expanded to their current songs on every call.
        """
        songs = []
        albums = []

        for entry_id in entry_ids:
            entry_type, obj = cls.any(entry_id)

            if entry_type == 'playlist':
                playlist = cls.playlist(entry_id)
                if playlist:
                    songs.extend(cls.playlist_songs(playlist['tracks']))
            elif entry_type == 'song' and obj:
                songs.append(obj)
            elif entry_type == 'album' and obj:
                albums.append(obj)

        return list({song.id: song for song in songs}.values()), albums


Resolve._method_map = {
    'song': Resolve.song,
    'album': Resolve.album,
    'radio': Resolve.radio,
    'podcast_channel': Resolve.podcast_channel,
    'podcast_episode': Resolve.podcast_episode,
}
