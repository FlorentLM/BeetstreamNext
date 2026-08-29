import base64
import os
from pathlib import Path
import binascii
from typing import Optional, Tuple, Dict, List, Any, Sequence
import flask
from beets.library import LibModel, Item

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.utils.text import split_beets_multi, validate_mbid
from beetsplug.beetstreamnext.utils.system import path_hash
from beetsplug.beetstreamnext.utils.db import get_beets_schema, chunked_query
from beetsplug.beetstreamnext.core.database import database



def beets_abspath(item: Dict | Item | Any) -> Path:
    """
    Beets sometimes stores paths relative to its 'directory' config.
    This resolves to an absolute path.
    """
    path_obj = Path(os.fsdecode(item.get('path', b'')))
    if not path_obj.is_absolute():
        path_obj = app.config['root_directory'] / path_obj
    return path_obj


def standardise_datadict(obj: Dict | LibModel | Item | Any) -> dict:
    """Standardise input (Beets Item/Album or sqlite3.Row) into a dict."""
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
    """Lookup MBID, sort name and roles for a given artist name."""
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
            """SELECT mb_albumartistid, albumartist_sort FROM albums WHERE albumartist = ? LIMIT 1""",
            (name,)
        )
        if album_rows:
            roles.append('albumartist')
            row = album_rows[0]
            if row[0]: mbid = validate_mbid(row[0])
            if row[1]: sort_name = row[1]

        item_rows = tx.query(
            """SELECT mb_artistid, artist_sort FROM items WHERE artist = ? LIMIT 1""",
            (name,)
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


def _mint_song_id(mb_trackid: Any, mb_releasetrackid: Any, path: Any, beets_id: Any, root_directory) -> str:
    """
    Make the stable BSN song id for a beets item: prefers whatever external database id beets recorded
    (MusicBrainz, Deezer, Spotify etc -- beets always writes them in the mb_* field)

    Falls back to a hash of the item's path (relative to root_directory), or to the raw beets row
    id if neither is available (but this one is unstable across reimports).
    """
    mbid = str(mb_releasetrackid or mb_trackid or '').strip()
    if mbid:
        encoded = base64.urlsafe_b64encode(mbid.encode('utf-8')).rstrip(b'=').decode('utf-8')
        return f"{IDMapper.SNG_MBID_PREF}{encoded}"

    hash = path_hash(path, root_directory)
    if hash:
        return f"{IDMapper.SNG_HASH_PREF}{hash}"

    return f"{IDMapper.SNG_ID_PREF}{beets_id}"


class IDMapper:
    """
    Handles translation between Beets internal IDs and Subsonic REST IDs.
    """

    ART_MBID_PREF = 'ar-m-'     # ar-m-<base64url(mbid)>  preferred if mbid is known
    ART_NAME_PREF = 'ar-n-'     # ar-n-<base64url(name)>  fallback
    SNG_ID_PREF = 'sg-'         # legacy: sg-<raw beets row id> (decode-only)
    SNG_MBID_PREF = 'sg-m-'     # sg-m-<base64url(mb_releasetrackid or mb_trackid)>
    SNG_HASH_PREF = 'sg-h-'     # sg-h-<hash of path relative to root_directory>

    ALB_ID_PREF = 'al-'
    PLY_ID_PREF = 'pl-'
    RAD_ID_PREF = 'ir-'
    PCH_ID_PREF = 'pc-'   # podcast channel: pc-<db id>
    PEP_ID_PREF = 'pe-'   # podcast episode: pe-<db id>

    @staticmethod
    def _to_beets_int(subsonic_id: str, prefix: str) -> int | None:
        sid = str(subsonic_id)
        if not sid.startswith(prefix):
            return None
        try:
            return int(sid[len(prefix):])
        except (ValueError, IndexError):
            return None

    @classmethod
    def mint_artist(cls, name_or_mbid: Any, is_mbid: bool = True) -> str:
        encoded = base64.urlsafe_b64encode(str(name_or_mbid).encode('utf-8')).rstrip(b'=').decode('utf-8')
        prefix = cls.ART_MBID_PREF if is_mbid else cls.ART_NAME_PREF
        return f"{prefix}{encoded}"

    @classmethod
    def decode_song_mbid(cls, subsonic_id: str) -> str:
        payload = subsonic_id[len(cls.SNG_MBID_PREF):]
        padding = (4 - len(payload) % 4) % 4
        try:
            return base64.urlsafe_b64decode(payload + '=' * padding).decode('utf-8')
        except (binascii.Error, UnicodeDecodeError):
            return ''

    @classmethod
    def decode_artist_mbid(cls, subsonic_id: str) -> Tuple[str, bool]:
        sid = str(subsonic_id)
        if sid.startswith(cls.ART_MBID_PREF):
            payload, is_mbid = sid[len(cls.ART_MBID_PREF):], True
        elif sid.startswith(cls.ART_NAME_PREF):
            payload, is_mbid = sid[len(cls.ART_NAME_PREF):], False
        else:
            return '', False

        padding = (4 - len(payload) % 4) % 4
        try:
            value = base64.urlsafe_b64decode(payload + '=' * padding).decode('utf-8')
            return value, is_mbid
        except (binascii.Error, UnicodeDecodeError):
            return '', False

    @classmethod
    def mint_album(cls, beets_id: int) -> str:
        return f"{cls.ALB_ID_PREF}{beets_id}"

    @classmethod
    def resolve_album(cls, subsonic_id: str) -> Optional[LibModel]:
        """Decode a Subsonic album id and fetch the beets album in one step."""
        beets_id = cls._to_beets_int(subsonic_id, cls.ALB_ID_PREF)
        return flask.g.lib.get_album(beets_id) if beets_id is not None else None

    @classmethod
    def mint_song(cls, song: dict) -> str:
        return _mint_song_id(
            song.get('mb_trackid'),
            song.get('mb_releasetrackid'),
            song.get('path'),
            song.get('id', 0),
            app.config['root_directory']
        )

    @classmethod
    def resolve_song(cls, subsonic_id: str) -> Optional[Item]:
        """
        Decode a Subsonic song id (mbid-tier, hash-tier, or legacy row id) and fetch the
        beets item in one step. Unlike the pure decode methods this needs a database lookup
        for the mbid/hash tiers, since those don't encode the row id directly.
        """
        sid = str(subsonic_id)

        if sid.startswith(cls.SNG_MBID_PREF):
            mbid = cls.decode_song_mbid(sid)
            if not mbid:
                return None
            with flask.g.lib.transaction() as tx:
                rows = tx.query(
                    """SELECT id FROM items WHERE mb_releasetrackid = ? OR mb_trackid = ? LIMIT 1""",
                    (mbid, mbid)
                )
            return flask.g.lib.get_item(rows[0][0]) if rows else None

        if sid.startswith(cls.SNG_HASH_PREF):
            target_hash = sid[len(cls.SNG_HASH_PREF):]
            root_directory = app.config['root_directory']
            with flask.g.lib.transaction() as tx:
                candidates = tx.query(
                    """
                    SELECT id, path FROM items
                    WHERE (mb_releasetrackid IS NULL OR mb_releasetrackid = '')
                      AND (mb_trackid IS NULL OR mb_trackid = '')
                    """
                )
            for row in candidates:
                if path_hash(row[1], root_directory) == target_hash:
                    return flask.g.lib.get_item(row[0])
            return None

        beets_id = cls._to_beets_int(sid, cls.SNG_ID_PREF)
        return flask.g.lib.get_item(beets_id) if beets_id is not None else None

    @classmethod
    def resolve_songs_bulk(cls, subsonic_ids: Sequence[str]) -> Dict[str, Item]:
        """Batched version of resolve_song()."""
        result: Dict[str, Item] = {}

        by_mbid: Dict[str, List[str]] = {}
        by_hash: Dict[str, List[str]] = {}
        by_int: Dict[int, List[str]] = {}

        for raw in subsonic_ids:
            sid = str(raw)
            if sid.startswith(cls.SNG_MBID_PREF):
                mbid = cls.decode_song_mbid(sid)
                if mbid:
                    by_mbid.setdefault(mbid, []).append(sid)
            elif sid.startswith(cls.SNG_HASH_PREF):
                by_hash.setdefault(sid[len(cls.SNG_HASH_PREF):], []).append(sid)
            else:
                beets_id = cls._to_beets_int(sid, cls.SNG_ID_PREF)
                if beets_id is not None:
                    by_int.setdefault(beets_id, []).append(sid)

        if by_mbid:
            mbids = list(by_mbid)
            with flask.g.lib.transaction() as tx:
                rows = chunked_query(tx, 'SELECT id, mb_releasetrackid, mb_trackid FROM items WHERE mb_releasetrackid IN ({q})', mbids)
                rows += chunked_query(tx, 'SELECT id, mb_releasetrackid, mb_trackid FROM items WHERE mb_trackid IN ({q})', mbids)
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
                    SELECT id, path FROM items
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
    def mint_radio(cls, db_id: int) -> str:
        return f'{cls.RAD_ID_PREF}{db_id}'

    @classmethod
    def resolve_radio(cls, subsonic_id: str) -> Optional[AttrDict]:
        """Decode a Subsonic radio id and fetch the station row (BSN's own db)."""
        radio_id = cls._to_beets_int(subsonic_id, cls.RAD_ID_PREF)
        if radio_id is None:
            return None
        with database() as db:
            row = db.execute(
                """SELECT * FROM internet_radio_stations WHERE id = ?""", (radio_id,)
            ).fetchone()
        return AttrDict(dict(row)) if row else None

    @classmethod
    def mint_podcast_channel(cls, db_id: int) -> str:
        return f'{cls.PCH_ID_PREF}{db_id}'

    @classmethod
    def resolve_podcast_channel(cls, subsonic_id: str) -> Optional[AttrDict]:
        """Decode a Subsonic podcast channel id and fetch its row (BSN's own db)."""
        channel_id = cls._to_beets_int(subsonic_id, cls.PCH_ID_PREF)
        if channel_id is None:
            return None
        with database() as db:
            row = db.execute("""SELECT * FROM podcast_channels WHERE id = ?""", (channel_id,)).fetchone()
        return AttrDict(dict(row)) if row else None

    @classmethod
    def mint_podcast_episode(cls, db_id: int) -> str:
        return f'{cls.PEP_ID_PREF}{db_id}'

    @classmethod
    def resolve_podcast_episode(cls, subsonic_id: str) -> Optional[AttrDict]:
        """Decode a Subsonic podcast episode id and fetch its row (BSN's own db)."""
        episode_id = cls._to_beets_int(subsonic_id, cls.PEP_ID_PREF)
        if episode_id is None:
            return None
        with database() as db:
            row = db.execute("""SELECT * FROM podcast_episodes WHERE id = ?""", (episode_id,)).fetchone()
        return AttrDict(dict(row)) if row else None

    @classmethod
    def get_type(cls, subsonic_id: str) -> str | None:
        """Returns the type of object this ID represents."""
        sid = str(subsonic_id)
        if sid.startswith((cls.ART_MBID_PREF, cls.ART_NAME_PREF)): return 'artist'
        if sid.startswith(cls.ALB_ID_PREF): return 'album'
        if sid.startswith(cls.SNG_ID_PREF): return 'song'
        if sid.startswith(cls.PLY_ID_PREF): return 'playlist'
        if sid.startswith(cls.RAD_ID_PREF): return 'radio'
        if sid.startswith(cls.PCH_ID_PREF): return 'podcastChannel'
        if sid.startswith(cls.PEP_ID_PREF): return 'podcastEpisode'
        return None

    @classmethod
    def resolve_artist(cls, req_id: str) -> Tuple[str, str] | None:
        """
        Returns (name, mbid) for an artist from any subsonic ID (artist, album, or song)
        (or None if ID can't be resolved)
        """
        if cls.get_type(req_id) == 'song':
            item = cls.resolve_song(req_id)
            if not item:
                return None

            name = item.get('albumartist') or item.get('artist') or ''
            mbid = validate_mbid(item.get('mb_albumartistid')) or validate_mbid(item.get('mb_artistid'))
            if not mbid:
                mbids = split_beets_multi(item.get('mb_albumartistids') or item.get('mb_artistids') or '')
                mbid = next(filter(None, (validate_mbid(m) for m in mbids)), '')

            return name, mbid

        if cls.get_type(req_id) == 'album':
            album = cls.resolve_album(req_id)
            if not album:
                return None

            name = album.get('albumartist') or ''
            mbid = validate_mbid(album.get('mb_albumartistid'))
            if not mbid:
                mbids = split_beets_multi(album.get('mb_albumartistids') or '')
                mbid = next(filter(None, (validate_mbid(m) for m in mbids)), '')

            return name, mbid

        if cls.get_type(req_id) == 'artist':
            value, is_mbid = cls.decode_artist_mbid(req_id)
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

            return artist_name, value   # value is the mbid

        else:
            artist_name = value
            meta = _get_artist_metadata(artist_name)
            return artist_name, meta['mbid']
