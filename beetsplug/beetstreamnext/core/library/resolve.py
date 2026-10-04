from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any, Sequence, Callable
import flask
from beets.library import LibModel, Item

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.library.ids import IDs, TYPES_PLAYABLE
from beetsplug.beetstreamnext.utils.text import split_beets_multi, validate_mbid
from beetsplug.beetstreamnext.utils.system import path_hash, resolve_path
from beetsplug.beetstreamnext.utils.db import get_beets_schema, chunked_query


def beets_abspath(item: Dict | Item | Any) -> Path:
    """
    Beets sometimes stores paths relative to its 'directory' config.
    This resolves to an absolute path.
    """
    return resolve_path(item.get('path', b''), app.config['root_directory'])


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


def standardise_datadict(obj: Dict | LibModel | Item | Any) -> dict:
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


def get_artist_metadata(name: str) -> dict:
    """
    Lookup MBID, sort name and roles for a given artist name.
    """

    if not name:
        return {'mbid': '', 'sort_name': '', 'roles': []}

    cache = flask.g.setdefault('_artist_metadata_cache', {})
    if name in cache:
        return cache[name]

    mbid = ''
    sort_name = ''
    roles = []

    with flask.g.lib.transaction() as tx:
        album_rows = tx.query(
            """
            SELECT mb_albumartistid, albumartist_sort 
            FROM albums 
            WHERE albumartist = ? LIMIT 1
            """, (name,)
        )
        if album_rows:
            roles.append('albumartist')

            row = album_rows[0]
            if row[0]:
                mbid = validate_mbid(row[0])
            if row[1]:
                sort_name = row[1]

        item_rows = tx.query(
            """
            SELECT mb_artistid, artist_sort 
            FROM items 
            WHERE artist = ? LIMIT 1
            """, (name,)
        )
        if item_rows:
            roles.append('artist')

            row = item_rows[0]
            if not mbid and row[0]:
                mbid = validate_mbid(row[0])
            if not sort_name and row[1]:
                sort_name = row[1]

        # Check for secondary roles
        if not roles:
            if tx.query(
                    """
                    SELECT 1
                    FROM items
                    WHERE artists LIKE ?
                    LIMIT 1
                    """, (f"%{name}%",)):
                roles.append('artist')

        cols = get_beets_schema('items')

        comp_col = 'composers' if 'composers' in cols else ('composer' if 'composer' in cols else None)
        if comp_col and tx.query(
                f"""
                SELECT 1 FROM items
                WHERE {comp_col} = ? OR {comp_col}
                LIKE ?
                LIMIT 1
                """, (name, f"%{name}%")):
            roles.append('composer')

        lyr_col = 'lyricists' if 'lyricists' in cols else ('lyricist' if 'lyricist' in cols else None)
        if lyr_col and tx.query(
                f"""
                SELECT 1 FROM items
                WHERE {lyr_col} = ? OR {lyr_col}
                LIKE ?
                LIMIT 1
                """, (name, f"%{name}%")):
            roles.append('lyricist')

    result = {
        'mbid': mbid,
        'sort_name': sort_name or name,
        'roles': roles if roles else ['artist']
    }

    cache[name] = result
    return result


##
# Resolver: Decodes a Subsonic ID and fetches the Beets/db object it refers to


class Resolve:
    """
    Decode Subsonic IDs (minted by IDs) and fetch the Beets/db object they refer to.
    """

    _method_map: Dict[str, Callable] = {}

    @classmethod
    def artist(cls, req_id: str) -> Tuple[str, str] | None:
        """
        Decode any music Subsonic ID (artist, album, or song) and fetch the beets artist data.

        Note: Unlike the other Resolve methods, this doesn't return a db object because beets has no
        Artist row.

        Returns (name, mbid), or None if ID can't be resolved.
        """
        entry_type, obj = cls.any(req_id)

        if entry_type == 'song':
            if not obj:
                return None

            name = obj.get('albumartist') or obj.get('artist') or ''
            mbid = validate_mbid(obj.get('mb_albumartistid')) or validate_mbid(obj.get('mb_artistid'))
            if not mbid:
                mbids = split_beets_multi(obj.get('mb_albumartistids') or obj.get('mb_artistids') or '')
                mbid = next(filter(None, (validate_mbid(m) for m in mbids)), '')

            return name, mbid

        if entry_type == 'album':
            if not obj:
                return None

            name = obj.get('albumartist') or ''
            mbid = validate_mbid(obj.get('mb_albumartistid'))
            if not mbid:
                mbids = split_beets_multi(obj.get('mb_albumartistids') or '')
                mbid = next(filter(None, (validate_mbid(m) for m in mbids)), '')

            return name, mbid

        if entry_type == 'artist':
            value, kind = IDs.decode_artist(req_id)
        else:
            value, kind = req_id, 'name'

        if kind == 'mbid':
            with flask.g.lib.transaction() as tx:
                # Prefer solo credit row for this MBID over a joint one
                rows = tx.query(
                    """
                    SELECT albumartist, albumartists
                    FROM albums
                    WHERE mb_albumartistid = ?
                    """, (value,)
                )
                solo_rows = [r for r in rows if not IDs.is_joint_credit(r[1], r[0])]
                pick = solo_rows[0] if solo_rows else (rows[0] if rows else None)

                if not pick:  # fallback to items table
                    rows = tx.query(
                        """
                        SELECT artist
                        FROM items
                        WHERE mb_artistid = ?
                        LIMIT 1
                        """, (value,)
                    )
                    pick = rows[0] if rows else None

            artist_name = pick[0] if pick else ''
            if not artist_name:
                return None

            return artist_name, value  # value is the mbid

        elif kind == 'hash':
            with flask.g.lib.transaction() as tx:
                candidates = tx.query(
                    """
                    SELECT albumartist
                    FROM albums
                    WHERE albumartist IS NOT NULL
                    GROUP BY albumartist
                    """
                )

            for row in candidates:
                name = row[0] or ''
                if name and hashlib.sha1(name.encode('utf-8')).hexdigest()[:16] == value:
                    return name, ''

            return None

        else:
            artist_name = value
            meta = get_artist_metadata(artist_name)
            return artist_name, meta['mbid']

    @classmethod
    def album(cls, subsonic_id: str) -> Optional[LibModel]:
        """
        Decode a Subsonic album ID (mbid, hash, or legacy row id) and fetch the beets album data.
        """
        value, kind = IDs.decode_album(subsonic_id)

        if kind == 'mbid':
            with flask.g.lib.transaction() as tx:
                rows = tx.query(
                    """
                    SELECT id
                    FROM albums
                    WHERE mb_albumid = ?
                    LIMIT 1
                    """, (value,)
                )
            return flask.g.lib.get_album(rows[0][0]) if rows else None

        if kind == 'hash':
            with flask.g.lib.transaction() as tx:
                candidates = tx.query(
                    """
                    SELECT id, albumartist, album
                    FROM albums
                    WHERE mb_albumid IS NULL OR mb_albumid = ''
                    """
                )

            for row in candidates:
                key = f"{row[1] or ''}\x1f{row[2] or ''}"
                if hashlib.sha1(key.encode('utf-8')).hexdigest()[:16] == value:
                    return flask.g.lib.get_album(row[0])

            return None

        if kind == 'legacy_int':
            return flask.g.lib.get_album(value)

        return None

    @classmethod
    def song(cls, subsonic_id: str) -> Optional[Item]:
        """
        Decode a Subsonic song ID (mbid, hash, or legacy row id) and fetch the beets song data.
        """
        value, kind = IDs.decode_song(subsonic_id)

        if kind == 'mbid':
            with flask.g.lib.transaction() as tx:
                rows = tx.query(
                    """
                    SELECT id
                    FROM items
                    WHERE mb_releasetrackid = ?
                       OR mb_trackid = ?
                    LIMIT 1
                    """, (value, value)
                )
            return flask.g.lib.get_item(rows[0][0]) if rows else None

        if kind == 'hash':
            root_directory = app.config['root_directory']

            with flask.g.lib.transaction() as tx:
                candidates = tx.query(
                    """
                    SELECT id, path
                    FROM items
                    WHERE (mb_releasetrackid IS NULL OR mb_releasetrackid = '')
                      AND (mb_trackid IS NULL OR mb_trackid = '')
                    """
                )

            for row in candidates:
                if path_hash(row[1], root_directory) == value:
                    return flask.g.lib.get_item(row[0])

            return None

        if kind == 'legacy_int':
            return flask.g.lib.get_item(value)

        return None

    @classmethod
    def songs(cls, subsonic_ids: Sequence[str]) -> Dict[str, Item]:
        """
        Batched song resolving. Returns {id: song_object},
        """

        result: Dict[str, Item] = {}

        by_mbid: Dict[str, List[str]] = {}
        by_hash: Dict[str, List[str]] = {}
        by_int: Dict[int, List[str]] = {}

        for raw in subsonic_ids:
            sid = str(raw)
            value, kind = IDs.decode_song(sid)

            if kind == 'mbid':
                by_mbid.setdefault(value, []).append(sid)
            elif kind == 'hash':
                by_hash.setdefault(value, []).append(sid)
            elif kind == 'legacy_int':
                by_int.setdefault(value, []).append(sid)

        if by_mbid:
            mbids = list(by_mbid)
            with flask.g.lib.transaction() as tx:
                rows = chunked_query(tx,
                    """
                    SELECT id, mb_releasetrackid, mb_trackid 
                    FROM items 
                    WHERE mb_releasetrackid IN ({q})
                    """, mbids
                )
                rows += chunked_query(tx,
                    """
                    SELECT id, mb_releasetrackid, mb_trackid 
                    FROM items 
                    WHERE mb_trackid IN ({q})
                    """, mbids
                )
            seen_rows = set()
            for row in rows:
                if row[0] in seen_rows:
                    continue
                seen_rows.add(row[0])
                key = row[1] or row[2]
                item = flask.g.lib.get_item(row[0])
                for sid in by_mbid.get(key, []):
                    result[sid] = item

        if by_hash:
            root_directory = app.config['root_directory']
            with flask.g.lib.transaction() as tx:
                candidates = tx.query(
                    """
                    SELECT id, path
                    FROM items
                    WHERE (mb_releasetrackid IS NULL OR mb_releasetrackid = '')
                      AND (mb_trackid IS NULL OR mb_trackid = '')
                    """
                )
            for row in candidates:
                h = path_hash(row[1], root_directory)
                if h in by_hash:
                    item = flask.g.lib.get_item(row[0])
                    for sid in by_hash[h]:
                        result[sid] = item

        if by_int:
            with flask.g.lib.transaction() as tx:
                rows = chunked_query(tx, 'SELECT id FROM items WHERE id IN ({q})', list(by_int))
            for row in rows:
                item = flask.g.lib.get_item(row[0])
                for sid in by_int.get(row[0], []):
                    result[sid] = item

        return result

    @classmethod
    def radio(cls, subsonic_id: str) -> Optional[AttrDict]:
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
    def podcast_channel(cls, subsonic_id: str) -> Optional[AttrDict]:
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
    def podcast_episode(cls, subsonic_id: str) -> Optional[AttrDict]:
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
    def any(cls, subsonic_id: str) -> Tuple[Optional[str], Optional[Any]]:
        """
        Decode any Subsonic ID and fetch its data object. Returns (type, object).

        Note: 'object' is None when the ID can't be resolved, or for a type that
        isn't a plain id->db lookup (like artist or playlist).
        """
        entry_type = IDs.decode_type(subsonic_id)
        resolver = cls._method_map.get(entry_type)
        return entry_type, (resolver(subsonic_id) if resolver else None)

    @classmethod
    def multiple(cls, subsonic_ids: Sequence[str]) -> Dict[str, Tuple[str, Any]]:
        """
        Batched resolve for a (possibly heterogeneous) list of IDs. Returns {id: (type, object)},
        omitting anything that didn't resolve.

        TODO: Only songs are bulk-fetched, maybe this should be extended to everything?
        """
        songs = cls.songs([sid for sid in subsonic_ids if IDs.decode_type(sid) == 'song'])

        result: Dict[str, Tuple[str, Any]] = {}
        for sid in subsonic_ids:
            entry_type = IDs.decode_type(sid)
            obj = songs.get(sid) if entry_type == 'song' else cls.any(sid)[1]
            if obj is not None:
                result[sid] = (entry_type, obj)

        return result

    @classmethod
    def playable(cls, entry_id: str, pre_resolved: Optional[Dict[str, Tuple[str, Any]]] = None) -> Optional[
        Tuple[str, str]]:
        """
        Resolve any playable Subsonic ID (song, radio station, or podcast episode) to (id, local path or URL).

        Args:
            - pre_resolved: An optional {id: (type, object)} map from a prior resolve_many() call.
            Falls back to a single-item resolve() when it's not given or doesn't have the id.
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

            return IDs.encode_song(standardise_datadict(obj)), path

        if entry_type == 'radio':
            if not obj.get('stream_url'):
                bsn_logger.warning(f'Radio station {entry_id!r} has no stream url, skipping.')
                return None
            return entry_id, obj['stream_url']

        if entry_type == 'podcast_episode':
            if obj.get('status') == 'completed' and obj.get('file_path'):
                return entry_id, obj['file_path']
            if obj.get('audio_url'):
                return entry_id, obj['audio_url']

            bsn_logger.warning(f'Podcast episode {entry_id!r} has no playable source, skipping.')
            return None

    @classmethod
    def playables(cls, entry_ids: Sequence[str]) -> List[Tuple[str, str]]:
        """
        Batched playable resolving. Resolves a list of playable Subsonic IDs
        to (id, local path or URL) pairs, skipping unresolvable ones.
        """
        resolved = cls.multiple(entry_ids)
        return [item for entry_id in entry_ids if (item := cls.playable(entry_id, resolved)) is not None]

    @classmethod
    def shared_items(cls, entry_ids: Sequence[str]) -> Tuple[List[Item], List[LibModel]]:
        """
        Returns a public share's entry IDs, split into song items and album objects.
        """
        songs = []
        albums = []

        for entry_id in entry_ids:
            entry_type, obj = cls.any(entry_id)

            if entry_type == 'song' and obj:
                songs.append(obj)
            elif entry_type == 'album' and obj:
                albums.append(obj)

        return songs, albums


Resolve._method_map = {
    'song': Resolve.song,
    'album': Resolve.album,
    'radio': Resolve.radio,
    'podcast_channel': Resolve.podcast_channel,
    'podcast_episode': Resolve.podcast_episode,
}
