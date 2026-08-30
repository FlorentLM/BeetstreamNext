import base64
import os
from pathlib import Path
import binascii
from typing import TYPE_CHECKING, Optional, Tuple, Dict, List, Any, Sequence, Callable
import flask
from beets.library import LibModel, Item

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.utils.text import split_beets_multi, validate_mbid
from beetsplug.beetstreamnext.utils.general import timestamp_to_iso, genres_formatter, external_url
from beetsplug.beetstreamnext.utils.system import path_hash, get_mimetype
from beetsplug.beetstreamnext.utils.db import get_beets_schema, chunked_query
from beetsplug.beetstreamnext.core.database import database, write_beets_field
from beetsplug.beetstreamnext.core.external import query_musicbrainz, query_discogs
from beetsplug.beetstreamnext.core.cache import (
    preload_songs, preload_albums, one_rating, one_like, one_play_stats, avg_rating, get_song_counts
)
from beetsplug.beetstreamnext.core.images import tokenised_image_url

if TYPE_CHECKING:
    from beetsplug.beetstreamnext.core.playlists import Playlist


def beets_abspath(item: Dict | Item | Any) -> Path:
    """
    Beets sometimes stores paths relative to its 'directory' config.
    This resolves to an absolute path.
    """
    path_obj = Path(os.fsdecode(item.get('path', b'')))
    if not path_obj.is_absolute():
        path_obj = app.config['root_directory'] / path_obj
    return path_obj


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


def _get_artist_metadata(name: str) -> dict:
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
            if row[0]: mbid = validate_mbid(row[0])
            if row[1]: sort_name = row[1]

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
            if not mbid and row[0]: mbid = validate_mbid(row[0])
            if not sort_name and row[1]: sort_name = row[1]

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


def _get_artists(data: dict) -> Tuple[List[Dict], List[Dict], List[Dict], str]:
    """
    Split a song/album's raw artist/composer/lyricist/etc. fields into Subsonic ID3 artist refs.
    """

    artists_array = []
    album_artists_array = []
    contributors_array = []
    composers = []

    seen_artists = set()
    seen_album_artists = set()
    seen_contributors = set()

    def _process(raw_names: str, raw_mbids: str, target_list: list, seen_set: set, is_contributor: bool = False,
                 role: str = ''):
        if not raw_names:
            return

        names = split_beets_multi(raw_names)
        mbids = split_beets_multi(raw_mbids) if raw_mbids else []

        for i, name in enumerate(names):
            if not name:
                continue

            mbid = ''
            if i < len(mbids) and mbids[i]:
                mbid = validate_mbid(mbids[i])
            elif is_contributor:
                meta = _get_artist_metadata(name)
                mbid = meta['mbid']

            contributor_id = IDs.encode_artist(mbid or name, is_mbid=bool(mbid))
            if is_contributor:
                dedup_key = (contributor_id, role)
                if dedup_key not in seen_set:
                    seen_set.add(dedup_key)
                    target_list.append({
                        'role': role,
                        'artist': {
                            'id': contributor_id,
                            'name': name
                        }
                    })
                    if role == 'composer':
                        composers.append(name)
            else:
                dedup_key = contributor_id
                if dedup_key not in seen_set:
                    seen_set.add(dedup_key)
                    target_list.append({
                        'id': contributor_id,
                        'name': name
                    })

    _process(data.get('artists') or '', data.get('mb_artistids') or '', artists_array, seen_artists)
    _process(data.get('albumartists') or '', data.get('mb_albumartistids') or '', album_artists_array,
             seen_album_artists)

    _process(data.get('composers') or data.get('composer') or '', '', contributors_array, seen_contributors, True,
             'composer')
    _process(data.get('lyricists') or data.get('lyricist') or '', '', contributors_array, seen_contributors, True,
             'lyricist')
    _process(data.get('remixers') or data.get('remixer') or '', '', contributors_array, seen_contributors, True,
             'remixer')
    _process(data.get('arrangers') or data.get('arranger') or '', '', contributors_array, seen_contributors, True,
             'arranger')

    display_composer = ", ".join(composers)

    return artists_array, album_artists_array, contributors_array, display_composer


##
# Data types hierarchy

# Types that OpenSubsonic considers media (ID3-tag -ish)
TYPES_MEDIA = frozenset({'artist', 'album', 'song', 'podcast_episode'})

# These can be played (streamed)
TYPES_PLAYABLE = frozenset({'song', 'radio', 'podcast_episode'})

# These are derived (no direct db access) or have a specific object model
TYPES_SPECIAL = frozenset({'artist', 'playlist'})

# These have a plain id->db row/dict lookup
TYPES_ANY = frozenset(TYPES_PLAYABLE.union({'album', 'podcast_channel'}))


##
# IDs: Mints IDs from a Beets/db object, or decodes them to their raw value/type. No I/O.


class IDs:
    """
    Mints the stable Subsonic-facing IDs BSN hands out, and decodes them back to
    whatever raw value (beets row id, mbid, name...) they were minted from.
    """

    _ART_MBID_PREF = 'ar-m-'    # ar-m-<base64url(mbid)>  preferred if mbid is known
    _ART_NAME_PREF = 'ar-n-'    # ar-n-<base64url(name)>  fallback
    _SNG_ID_PREF = 'sg-'        # legacy: sg-<raw beets row id> (decode-only)
    _SNG_MBID_PREF = 'sg-m-'    # sg-m-<base64url(mb_releasetrackid or mb_trackid)>
    _SNG_HASH_PREF = 'sg-h-'    # sg-h-<hash of path relative to root_directory>

    _ALB_ID_PREF = 'al-'
    _PLY_ID_PREF = 'pl-'
    _RAD_ID_PREF = 'ir-'
    _PCH_ID_PREF = 'pc-'        # podcast channel: pc-<db id>
    _PEP_ID_PREF = 'pe-'        # podcast episode: pe-<db id>

    @staticmethod
    def decode_int(subsonic_id: str, prefix: str) -> int | None:
        """
        Strip a known prefix off a Subsonic ID and parse the remainder as an int.
        """
        sid = str(subsonic_id)
        if not sid.startswith(prefix):
            return None
        try:
            return int(sid[len(prefix):])
        except (ValueError, IndexError):
            return None

    @classmethod
    def decode_type(cls, subsonic_id: str) -> str | None:
        """Returns the type of object this ID represents."""
        sid = str(subsonic_id)
        if sid.startswith((cls._ART_MBID_PREF, cls._ART_NAME_PREF)): return 'artist'
        if sid.startswith(cls._ALB_ID_PREF): return 'album'
        if sid.startswith(cls._SNG_ID_PREF): return 'song'
        if sid.startswith(cls._PLY_ID_PREF): return 'playlist'
        if sid.startswith(cls._RAD_ID_PREF): return 'radio'
        if sid.startswith(cls._PCH_ID_PREF): return 'podcast_channel'
        if sid.startswith(cls._PEP_ID_PREF): return 'podcast_episode'
        return None

    @classmethod
    def decode_song(cls, subsonic_id: str) -> Tuple[Any, str] | Tuple[None, None]:
        """Decode any song id (mbid, hash, or legacy row id) into (value, kind)."""

        sid = str(subsonic_id)

        if sid.startswith(cls._SNG_MBID_PREF):
            payload = sid[len(cls._SNG_MBID_PREF):]
            padding = (4 - len(payload) % 4) % 4
            try:
                mbid = base64.urlsafe_b64decode(payload + '=' * padding).decode('utf-8')
            except (binascii.Error, UnicodeDecodeError):
                mbid = ''
            return (mbid, 'mbid') if mbid else (None, None)

        if sid.startswith(cls._SNG_HASH_PREF):
            return sid[len(cls._SNG_HASH_PREF):], 'hash'

        if sid.startswith(cls._SNG_ID_PREF):
            beets_id = cls.decode_int(sid, cls._SNG_ID_PREF)
            return (beets_id, 'legacy_int') if beets_id is not None else (None, None)

        return None, None

    @classmethod
    def decode_artist(cls, subsonic_id: str) -> Tuple[str, bool]:
        """Decode an artist ID back to (name or mbid, is_mbid)."""

        sid = str(subsonic_id)

        if sid.startswith(cls._ART_MBID_PREF):
            payload, is_mbid = sid[len(cls._ART_MBID_PREF):], True
        elif sid.startswith(cls._ART_NAME_PREF):
            payload, is_mbid = sid[len(cls._ART_NAME_PREF):], False
        else:
            return '', False

        padding = (4 - len(payload) % 4) % 4
        try:
            value = base64.urlsafe_b64decode(payload + '=' * padding).decode('utf-8')
            return value, is_mbid
        except (binascii.Error, UnicodeDecodeError):
            return '', False

    @classmethod
    def decode_playlist(cls, subsonic_id: str) -> str | None:
        """Decode a playlist ID back to its raw stem (dir id, suffix, and optional owner)."""
        sid = str(subsonic_id)
        if not sid.startswith(cls._PLY_ID_PREF):
            return None
        return sid[len(cls._PLY_ID_PREF):]

    @classmethod
    def encode_artist(cls, name_or_mbid: Any, is_mbid: bool = True) -> str:
        """
        Mint an artist ID from either a mbid or a plain name.
        """

        encoded = base64.urlsafe_b64encode(str(name_or_mbid).encode('utf-8')).rstrip(b'=').decode('utf-8')
        prefix = cls._ART_MBID_PREF if is_mbid else cls._ART_NAME_PREF
        return f"{prefix}{encoded}"

    @classmethod
    def encode_album(cls, beets_id: int) -> str:
        """
        Mint an album ID from a beets row ID.
        """
        return f"{cls._ALB_ID_PREF}{beets_id}"

    @classmethod
    def encode_song(cls, song: dict, _root_directory: Optional[bytes | str | Path] = None) -> str:
        """
        Mint the song ID for a beets item: prefers whatever external database ID beets
        recorded (MusicBrainz, Deezer, Spotify etc... beets always writes them in the mb_* field).

        Falls back to a hash of the item's path (relative to the root directory), or to the raw beets
        row ID if neither is available (but this one is unstable across reimports).

        Note: _root_directory is only necessary for the db migration callsite, it's called
            before app.config['root_directory'] is set
        """
        mbid = str(song.get('mb_releasetrackid') or song.get('mb_trackid') or '').strip()
        if mbid:
            encoded = base64.urlsafe_b64encode(mbid.encode('utf-8')).rstrip(b'=').decode('utf-8')
            return f"{cls._SNG_MBID_PREF}{encoded}"

        hash = path_hash(song.get('path'), _root_directory or app.config['root_directory'])
        if hash:
            return f"{cls._SNG_HASH_PREF}{hash}"

        return f"{cls._SNG_ID_PREF}{song.get('id', 0)}"

    @classmethod
    def encode_playlist(cls, dir_id: int, stem_suffix: str, owner: Optional[str] = None) -> str:
        """
        Mint a playlist ID from its directory ID and filename stem, optionally scoped to an owner.
        """
        if owner:
            return f"{cls._PLY_ID_PREF}{dir_id}-{owner}/{stem_suffix}"
        return f"{cls._PLY_ID_PREF}{dir_id}-{stem_suffix}"

    @classmethod
    def encode_radio(cls, db_id: int) -> str:
        """
        Mint a radio station ID from its db row ID.
        """
        return f'{cls._RAD_ID_PREF}{db_id}'

    @classmethod
    def encode_podcast_channel(cls, db_id: int) -> str:
        """
        Mint a podcast channel ID from its db row ID.
        """
        return f'{cls._PCH_ID_PREF}{db_id}'

    @classmethod
    def encode_podcast_episode(cls, db_id: int) -> str:
        """
        Mint a podcast episode ID from its db row ID.
        """
        return f'{cls._PEP_ID_PREF}{db_id}'


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
            value, is_mbid = IDs.decode_artist(req_id)
        else:
            value, is_mbid = req_id, False

        if is_mbid:
            with flask.g.lib.transaction() as tx:
                # Check albums first
                rows = tx.query(
                    """
                    SELECT albumartist
                    FROM albums
                    WHERE mb_albumartistid = ?
                    LIMIT 1
                    """, (value,)
                )
                if not rows:  # fallback to items table
                    rows = tx.query(
                        """
                        SELECT artist
                        FROM items
                        WHERE mb_artistid = ?
                        LIMIT 1
                        """, (value,)
                    )

            artist_name = rows[0][0] if rows else ''
            if not artist_name:
                return None

            return artist_name, value  # value is the mbid

        else:
            artist_name = value
            meta = _get_artist_metadata(artist_name)
            return artist_name, meta['mbid']

    @classmethod
    def album(cls, subsonic_id: str) -> Optional[LibModel]:
        """
        Decode a Subsonic album ID and fetch the beets album data.
        """
        beets_id = IDs.decode_int(subsonic_id, IDs._ALB_ID_PREF)
        return flask.g.lib.get_album(beets_id) if beets_id is not None else None

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


##
# Serialise: turn a resolved Beets/db object into its serialised Subsonic response dict.


class Serialise:
    """
    Maps a Beets/db object (as returned by the resolver) to its serialised Subsonic response dict.
    """

    _method_map: Dict[str, Callable] = {}

    @classmethod
    def _common(cls, beets_object: Dict | LibModel) -> dict:
        """
        Build the tag-derived fields (artist credits, genre, dates...) shared by song
        and album responses. Internal helper, not a member of the entry-type hierarchy.
        """

        data = standardise_datadict(beets_object)

        track_artist_name = data.get('artist') or data.get('albumartist') or ''

        main_ar_name = data.get('albumartist') or data.get('artist') or ''
        main_ar_mbid = validate_mbid(data.get('mb_albumartistid')) or validate_mbid(data.get('mb_artistid'))

        artist_id = IDs.encode_artist(main_ar_mbid or main_ar_name, is_mbid=bool(main_ar_mbid))

        artists, album_artists, contributors, display_composer = _get_artists(data)

        raw_genres = f"{data.get('genres') or ''};{data.get('genre') or ''}"
        formatted_genres = genres_formatter(raw_genres)

        main_genre = formatted_genres[0] if formatted_genres else ''
        genres_list = [{'name': g} for g in formatted_genres]

        subsonic_media = {
            'artist': track_artist_name,
            'artistId': artist_id,
            'displayArtist': track_artist_name,
            'displayAlbumArtist': main_ar_name,
            'artists': artists,
            'albumArtists': album_artists,
            'contributors': contributors,
            'displayComposer': display_composer,
            'album': data.get('album') or '',
            'year': data.get('year') or 0,
            'genre': main_genre,
            'genres': genres_list,
            'created': timestamp_to_iso(data.get('added')),
            'originalReleaseDate': {
                'year': data.get('original_year') or data.get('year') or 0,
                'month': data.get('original_month') or data.get('month') or 0,
                'day': data.get('original_day') or data.get('day') or 0
            },
            'releaseDate': {
                'year': data.get('year') or 0,
                'month': data.get('month') or 0,
                'day': data.get('day') or 0
            },
        }

        if display_composer:
            subsonic_media['displayComposer'] = display_composer

        return subsonic_media

    @classmethod
    def artist(cls, artist_name: str, with_albums: bool = True, prefetched: Optional[Dict] = None) -> dict:
        """
        Map a beets artist name to its serialised Subsonic ArtistID3 response dict.
        """

        # Priority: prefetched -> album query (when with_albums) -> standalone db query
        mbid = ''
        sort_name = artist_name
        album_count = 0
        albums = None

        if prefetched and artist_name in prefetched:
            pf = prefetched[artist_name]
            mbid = pf.get('mbid') or ''
            sort_name = pf.get('sort_name') or artist_name
            album_count = pf.get('album_count', 0)

        elif with_albums:
            albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))
            if albums:
                mbid = albums[0].get('mb_albumartistid', '') or ''
                sort_name = albums[0].get('albumartist_sort', '') or artist_name
            album_count = len(albums) if albums else 0

        else:
            with flask.g.lib.transaction() as tx:
                rows = tx.query(
                    """
                    SELECT COUNT(*), mb_albumartistid, albumartist_sort
                    FROM albums
                    WHERE albumartist = ?
                    GROUP BY albumartist
                    """, (artist_name,)
                )

            if rows:
                row = rows[0]
                album_count, mbid, sort_name = row[0], row[1] or '', row[2] or artist_name

        meta = _get_artist_metadata(artist_name)
        mbid = validate_mbid(mbid) or meta['mbid']  # meta['mbid'] is already validated by _artist_metadata()
        sort_name = sort_name if sort_name != artist_name else meta['sort_name']
        roles = meta['roles']

        subsonic_artist_id = IDs.encode_artist(mbid or artist_name, is_mbid=bool(mbid))

        subsonic_artist = {
            'id': subsonic_artist_id,
            'name': artist_name,
            'sortName': sort_name,
            'roles': roles,
            'musicBrainzId': mbid,
            'title': artist_name,
            'albumCount': album_count,
            'coverArt': subsonic_artist_id,
            'userRating': one_rating(subsonic_artist_id),
            'artistImageUrl': tokenised_image_url(subsonic_artist_id),
            'mediaType': 'artist'
        }

        if with_albums:

            if albums is None:  # already fetched above if not prefetched
                albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))

            preload_albums(albums)
            song_counts = get_song_counts(albums)

            subsonic_artist['album'] = [
                cls.album(alb, include_songs=False, song_counts=song_counts)
                for alb in albums
            ]

        liked_at = one_like(subsonic_artist_id)
        if liked_at:
            subsonic_artist['starred'] = timestamp_to_iso(liked_at)

        return subsonic_artist

    @classmethod
    def album(cls, album_object: Dict | LibModel, include_songs: bool = True,
              song_counts: Optional[Dict] = None) -> dict:
        """
        Map a beets album object to its serialised Subsonic AlbumID3 response dict.
        """

        data = standardise_datadict(album_object)

        beets_album_id = data.get('id', 0)
        subsonic_album_id = IDs.encode_album(beets_album_id)
        album_name = data.get('album', '')

        subsonic_album = cls._common(data)

        album_specific = {
            'id': subsonic_album_id,
            'musicBrainzId': validate_mbid(data.get('mb_albumid')),
            'name': album_name,
            'sortName': album_name,
            'coverArt': subsonic_album_id,
            'userRating': one_rating(subsonic_album_id),
            'isCompilation': bool(data.get('comp', False)),

            # These are only needed when part of a directory response
            'isDir': True,
            'parent': subsonic_album['artistId'],

            # Title field is required for Child responses (also used in albumList or albumList2 responses)
            'title': album_name,

            # This is only needed when part of a Child response
            'mediaType': 'album'
        }
        subsonic_album.update(album_specific)

        version = data.get('version')  # 'Deluxe Edition', 'Japanese Expanded Edition', etc.
        if not version and subsonic_album['musicBrainzId'] and app.config.get('fetch_album_version'):
            mb_data = query_musicbrainz(subsonic_album['musicBrainzId'], data_type='album')
            version = mb_data.get('disambiguation')

        if version:
            subsonic_album['version'] = version
            if app.config.get('save_album_version'):
                write_beets_field('album', data['id'], 'version', version, allow_flex=True)

        # Add labels if possible
        label = data.get('label', '')
        if label:
            subsonic_album['recordLabels'] = [{'name': label}]

        # Add release types if possible
        rt = data.get('albumtypes', '') or data.get('albumtype', '')
        release_types = [s.title() for s in split_beets_multi(rt)]
        if release_types:
            subsonic_album['releaseTypes'] = release_types

        # Add multi-disc info if needed
        nb_discs = data.get('disctotal', 1)
        if nb_discs > 1:
            subsonic_album["discTitles"] = [
                {'disc': d, 'title': ' - '.join(filter(None, [data.get('album', None), f'Disc {d + 1}']))}
                for d in range(nb_discs)
            ]

        # Songs should be included when in:
        # - AlbumID3WithSongs response
        # - directory response ('song' key needs to be renamed to 'child')

        if song_counts and beets_album_id in song_counts:
            subsonic_album['songCount'], subsonic_album['duration'] = song_counts[beets_album_id]

        elif not include_songs:
            # No need for full song objects, only SQL count
            with flask.g.lib.transaction() as tx:
                rows = tx.query(
                    """
                    SELECT COUNT(*), SUM(length)
                    FROM items
                    WHERE album_id = ?
                    """, (beets_album_id,)
                )

            if rows:
                count, duration = rows[0][:2]
                subsonic_album['songCount'] = count
                subsonic_album['duration'] = round(duration or 0)
            else:
                subsonic_album['songCount'] = 0
                subsonic_album['duration'] = 0

        if include_songs:
            # Need song details
            songs = list(flask.g.lib.items(f'album_id:{beets_album_id}'))

            preload_songs(songs)

            if 'songCount' not in subsonic_album:
                subsonic_album['songCount'] = len(songs)
                subsonic_album['duration'] = round(sum(s.get('length', 0) for s in songs))

            song_filesizes = {}
            if songs:
                try:
                    album_dir = os.path.dirname(os.fsdecode(songs[0].path))
                    with os.scandir(album_dir) as it:
                        for entry in it:
                            if entry.is_file():
                                song_filesizes[entry.path] = entry.stat().st_size
                except Exception as e:
                    bsn_logger.debug(f"Filesize prefetch failed: {e}")

            songs.sort(key=lambda s: (s.get('disc', 1), s.get('track', 1)))
            subsonic_album['song'] = [cls.song(s, prefetched_sizes=song_filesizes) for s in songs]

        local_avg, local_count = avg_rating(subsonic_album_id)
        discogs_mode = app.config.get('discogs_ratings', 'off')

        discogs_avg = None
        if discogs_mode != 'off' and data.get('discogs_albumid') and (discogs_mode == 'prefer' or not local_count):
            rating = query_discogs(data['discogs_albumid']).get('community', {}).get('rating', {})
            if rating.get('average'):
                discogs_avg = round(rating['average'], 2)

        if discogs_mode == 'prefer' and discogs_avg is not None:
            subsonic_album['averageRating'] = discogs_avg
        elif local_count:
            subsonic_album['averageRating'] = local_avg
        elif discogs_avg is not None:
            subsonic_album['averageRating'] = discogs_avg
        else:
            subsonic_album['averageRating'] = 0

        # Starred status
        liked_at = one_like(subsonic_album_id)
        if liked_at:
            subsonic_album['starred'] = timestamp_to_iso(liked_at)

        return subsonic_album

    @classmethod
    def song(cls, song_object: Dict | LibModel | Item, prefetched_sizes: Optional[Dict[str, int]] = None) -> dict:
        """
        Map a beets song item to its serialised Subsonic Child response dict.
        """

        data = standardise_datadict(song_object)

        song_id = IDs.encode_song(data)
        song_title = data.get('title') or ''

        subsonic_song = cls._common(data)

        song_filepath = os.fsdecode(data.get('path', b''))
        album_id = IDs.encode_album(data.get('album_id', 0))

        song_specific = {
            'id': song_id,
            'musicBrainzId': validate_mbid(data.get('mb_releasetrackid')) or validate_mbid(data.get('mb_trackid')),
            'name': song_title,
            'sortName': song_title,
            'albumId': album_id,
            'coverArt': album_id or song_id,
            'language': data.get('language') or '',
            'path': song_filepath,
            'userRating': one_rating(song_id),
            'duration': round(data.get('length') or 0),
            'bpm': data.get('bpm') or 0,
            'bitRate': round((data.get('bitrate') or 0) / 1000),
            'bitDepth': data.get('bitdepth') or 0,
            'samplingRate': data.get('samplerate') or 0,
            'channelCount': data.get('channels') or 2,
            'discNumber': data.get('disc') or 1,
            'comment': data.get('comment') or '',

            # These are only needed when part of a directory response
            'isDir': False,
            'parent': album_id or subsonic_song['artistId'],

            'isVideo': False,
            'type': 'music',

            # Title field is required for Child responses
            'title': song_title,

            # This is only needed when part of a Child response
            'mediaType': 'song'
        }
        subsonic_song.update(song_specific)

        isrc_raw = data.get('isrc') or ''
        if isrc_raw:
            subsonic_song['isrc'] = split_beets_multi(isrc_raw)

        work = data.get('work') or ''
        if work:
            work_obj = {'name': work}
            mb_workid = data.get('mb_workid')
            if mb_workid:
                work_obj['musicBrainzId'] = mb_workid
            subsonic_song['works'] = [work_obj]

        tg = data.get('rg_track_gain')
        ag = data.get('rg_album_gain')

        # r128 fields are stored as LU/dB * 256
        if tg is None:
            r128_tg = data.get('r128_track_gain')
            if r128_tg is not None:
                tg = float(r128_tg) / 256.0

        if ag is None:
            r128_ag = data.get('r128_album_gain')
            if r128_ag is not None:
                ag = float(r128_ag) / 256.0

        # Peaks are stored as linear ratios 0.0 to 1.0
        tp = data.get('rg_track_peak')
        ap = data.get('rg_album_peak')

        if tg is not None or ag is not None:
            track_peak = min(max(float(tp or 1.0), 0.0), 1.0)
            album_peak = min(max(float(ap or 1.0), 0.0), 1.0)

            subsonic_song['replayGain'] = {
                'trackGain': round(float(tg or 0.0), 2),
                'albumGain': round(float(ag or 0.0), 2),
                'trackPeak': track_peak,
                'albumPeak': album_peak,
                'baseGain': 0.0
            }

        track_nb = data.get('track')
        if track_nb:
            subsonic_song['track'] = track_nb

        suffix = (data.get('format') or '').lower()
        if not suffix and song_filepath:
            suffix = song_filepath.rsplit('.', 1)[-1].lower()
        subsonic_song['suffix'] = suffix or 'mp3'
        subsonic_song['contentType'] = get_mimetype(song_filepath or suffix)

        if prefetched_sizes and song_filepath in prefetched_sizes:
            subsonic_song['size'] = prefetched_sizes[song_filepath]
        else:
            bitrate = data.get('bitrate') or 0
            length = data.get('length') or 0
            subsonic_song['size'] = round((bitrate * length) / 8)

            # only hit the disk if bitrate/length missing
            if subsonic_song['size'] == 0:
                try:
                    subsonic_song['size'] = os.path.getsize(song_filepath)
                except Exception:
                    pass

        stats = one_play_stats(song_id)
        if stats:
            subsonic_song['playCount'] = stats['play_count']
            if stats['last_played']:
                subsonic_song['played'] = timestamp_to_iso(stats['last_played'])

        liked_at = one_like(subsonic_song['id'])
        if liked_at:
            subsonic_song['starred'] = timestamp_to_iso(liked_at)

        return subsonic_song

    @classmethod
    def playlist(cls, playlist: 'Playlist', include_songs: bool = False) -> dict:
        """
        Map a Playlist object to its serialised Subsonic PlaylistWithSongs response dict.
        """
        subsonic_playlist = {
            'id': playlist.id,
            'name': playlist.name,
            'comment': playlist.comment,
            'songCount': playlist.song_count,
            'duration': playlist.duration,
            'created': timestamp_to_iso(playlist.ctime),
            'changed': timestamp_to_iso(playlist.mtime),
            'owner': playlist.owner or playlist.creator or '',
            'public': playlist.owner is None,
            'coverArt': playlist.id,
        }
        if include_songs and playlist.songs:
            subsonic_playlist['entry'] = playlist.songs

        return subsonic_playlist

    @classmethod
    def radio(cls, row: dict) -> dict:
        """
        Map an internet radio station row to its serialised Subsonic InternetRadioStation dict.
        """
        station_id = IDs.encode_radio(row['id'])

        subsonic_radio_station = {
            'id': station_id,
            'name': row['name'],
            'streamUrl': row['stream_url'],
            'homePageUrl': row['homepage_url'] or '',
            'coverArt': station_id
        }
        return subsonic_radio_station

    @classmethod
    def podcast_channel(cls, row: dict, episodes: Optional[List[dict]] = None) -> dict:
        channel_id = IDs.encode_podcast_channel(row['id'])

        subsonic_channel = {
            'id': channel_id,
            'url': row['url'],
            'title': row.get('title') or row['url'],
            'description': row.get('description') or '',
            'coverArt': channel_id,
            'originalImageUrl': row.get('image_url') or '',
            'status': row.get('status') or 'new',
        }

        if row.get('error_message'):
            subsonic_channel['errorMessage'] = row['error_message']

        if episodes is not None:
            subsonic_channel['episode'] = [cls.podcast_episode(ep, row) for ep in episodes]

        return subsonic_channel

    @classmethod
    def podcast_episode(cls, row: dict, channel: Optional[dict] = None) -> dict:

        episode_id = IDs.encode_podcast_episode(row['id'])
        channel_id = IDs.encode_podcast_channel(row['channel_id'])

        title = row.get('title') or ''
        channel_title = (channel or {}).get('title') or (channel or {}).get('channel_title') or ''

        subsonic_episode = {
            'id': episode_id,
            'parent': channel_id,
            'channelId': channel_id,
            'title': title,
            'name': title,
            'description': row.get('description') or '',
            'status': row.get('status') or 'new',
            'coverArt': channel_id,
            'isDir': False,
            'isVideo': False,
            'type': 'podcast',
            'mediaType': 'podcast',
            'duration': round(row.get('duration') or 0),
            'size': row.get('file_size') or 0,
        }

        if row.get('publish_date'):
            published_iso = timestamp_to_iso(row['publish_date'])
            if published_iso:
                subsonic_episode['publishDate'] = published_iso
                subsonic_episode['created'] = published_iso

        if channel_title:
            subsonic_episode['album'] = channel_title
            subsonic_episode['artist'] = channel_title

        if row.get('error_message'):
            subsonic_episode['errorMessage'] = row['error_message']

        if row.get('status') == 'completed' and row.get('file_path'):
            suffix = Path(row['file_path']).suffix.lstrip('.').lower() or 'mp3'
            subsonic_episode['suffix'] = suffix
            subsonic_episode['contentType'] = get_mimetype(row['file_path'])
            subsonic_episode['streamId'] = episode_id

        return subsonic_episode

    @classmethod
    def any(cls, entry_type: Optional[str], obj: Optional[Any]) -> Optional[dict]:
        """
        Map any resolvable Subsonic object (song, album, radio, podcast channel, or
        episode) to its serialised response dict, given the (type, object) pair
        returned by Resolve.any().
        """
        if obj is None:
            return None
        serialiser = cls._method_map.get(entry_type)
        return serialiser(obj) if serialiser else None

    @classmethod
    def playable(cls, entry_id: str, pre_resolved: Optional[Dict[str, Tuple[str, Any]]] = None) -> Optional[dict]:
        """
        Map any playable Subsonic ID (song, radio station, or podcast episode) to its serialised entry dict.

        Args:
            - pre_resolved: An optional {id: (type, object)} map from a prior resolve_many() call.
            Falls back to a single-item resolve() when it's not given or doesn't have the id.
        """

        entry_type, obj = (pre_resolved or {}).get(entry_id) or Resolve.any(entry_id)
        if obj is None or entry_type not in TYPES_PLAYABLE:
            return None

        if entry_type == 'song':
            return cls.song(obj)

        if entry_type == 'radio':
            return cls.radio(dict(obj))

        if entry_type == 'podcast_episode':
            channel = Resolve.podcast_channel(IDs.encode_podcast_channel(obj['channel_id']))
            return cls.podcast_episode(obj, channel)

    @classmethod
    def playables(cls, entry_ids: Sequence[str]) -> List[dict]:
        """
        Batched serialisation of playables. Maps a list of playable
        Subsonic IDs to their serialised entry dicts, skipping unresolvable ones.
        """
        resolved = Resolve.multiple(entry_ids)
        return [item for entry_id in entry_ids if (item := cls.playable(entry_id, resolved)) is not None]

    @classmethod
    def shared_items(cls, row: dict, entries: Sequence[str]) -> dict:
        """
        Map a public share row and its entry IDs to a serialised Subsonic Share dict.
        """

        songs, albums = Resolve.shared_items(entries)
        share_url = external_url(flask.url_for('public.share_view', share_id=row['id']))

        subsonic_share = {
            'id': row['id'],
            'url': share_url,
            'description': row['description'] or '',
            'username': row['username'],
            'created': timestamp_to_iso(row['created']),
            'expires': timestamp_to_iso(row['expires']),
            'visitCount': row['visit_count'],
            'entry': [cls.song(s) for s in songs] + [cls.album(a, include_songs=False) for a in albums]
        }
        return subsonic_share


Serialise._method_map = {
    'song': Serialise.song,
    'album': Serialise.album,
    'radio': Serialise.radio,
    'podcast_channel': Serialise.podcast_channel,
    'podcast_episode': Serialise.podcast_episode,
}