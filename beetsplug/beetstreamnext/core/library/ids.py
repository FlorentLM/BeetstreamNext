from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Optional, Tuple, Any

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.utils.text import split_beets_multi
from beetsplug.beetstreamnext.utils.system import path_hash


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
    _ART_HASH_PREF = 'ar-h-'    # ar-h-<hash of the full joint-credit text>, for multi-artist entries
    _SNG_ID_PREF = 'sg-'        # legacy: sg-<raw beets row id> (decode-only)
    _SNG_MBID_PREF = 'sg-m-'    # sg-m-<base64url(mb_releasetrackid or mb_trackid)>
    _SNG_HASH_PREF = 'sg-h-'    # sg-h-<hash of path relative to root_directory>

    _ALB_ID_PREF = 'al-'         # legacy: al-<raw beets row id> (decode-only)
    _ALB_MBID_PREF = 'al-m-'     # al-m-<base64url(mb_albumid)>
    _ALB_HASH_PREF = 'al-h-'     # al-h-<hash of "albumartist\x1falbum">

    _PLY_ID_PREF = 'pl-'
    _RAD_ID_PREF = 'ir-'
    _PCH_ID_PREF = 'pc-'        # podcast channel: pc-<db id>
    _PEP_ID_PREF = 'pe-'        # podcast episode: pe-<db id>

    _TYPE_BY_PREFIX = (
        # (prefixes, type) pairs for decode_type()
        ((_ART_MBID_PREF, _ART_NAME_PREF, _ART_HASH_PREF), 'artist'),
        (_ALB_ID_PREF, 'album'),
        (_SNG_ID_PREF, 'song'),
        (_PLY_ID_PREF, 'playlist'),
        (_RAD_ID_PREF, 'radio'),
        (_PCH_ID_PREF, 'podcast_channel'),
        (_PEP_ID_PREF, 'podcast_episode'),
    )

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
        for prefixes, id_type in cls._TYPE_BY_PREFIX:
            if sid.startswith(prefixes):
                return id_type
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
            except ValueError:    # binascii.Error, UnicodeDecodeError and non-ASCII input
                mbid = ''
            return (mbid, 'mbid') if mbid else (None, None)

        if sid.startswith(cls._SNG_HASH_PREF):
            return sid[len(cls._SNG_HASH_PREF):], 'hash'

        if sid.startswith(cls._SNG_ID_PREF):
            beets_id = cls.decode_int(sid, cls._SNG_ID_PREF)
            return (beets_id, 'legacy_int') if beets_id is not None else (None, None)

        return None, None

    @classmethod
    def decode_album(cls, subsonic_id: str) -> Tuple[Any, str] | Tuple[None, None]:
        """Decode any album id (mbid, hash, or legacy row id) into (value, kind)."""

        sid = str(subsonic_id)

        if sid.startswith(cls._ALB_MBID_PREF):
            payload = sid[len(cls._ALB_MBID_PREF):]
            padding = (4 - len(payload) % 4) % 4
            try:
                mbid = base64.urlsafe_b64decode(payload + '=' * padding).decode('utf-8')
            except ValueError:    # binascii.Error, UnicodeDecodeError and non-ASCII input
                mbid = ''
            return (mbid, 'mbid') if mbid else (None, None)

        if sid.startswith(cls._ALB_HASH_PREF):
            return sid[len(cls._ALB_HASH_PREF):], 'hash'

        if sid.startswith(cls._ALB_ID_PREF):
            beets_id = cls.decode_int(sid, cls._ALB_ID_PREF)
            return (beets_id, 'legacy_int') if beets_id is not None else (None, None)

        return None, None

    @classmethod
    def decode_artist(cls, subsonic_id: str) -> Tuple[str, str]:
        """Decode an artist ID back to (value, kind), kind in {'mbid', 'name', 'hash'}."""

        sid = str(subsonic_id)

        if sid.startswith(cls._ART_HASH_PREF):
            return sid[len(cls._ART_HASH_PREF):], 'hash'

        if sid.startswith(cls._ART_MBID_PREF):
            payload, kind = sid[len(cls._ART_MBID_PREF):], 'mbid'
        elif sid.startswith(cls._ART_NAME_PREF):
            payload, kind = sid[len(cls._ART_NAME_PREF):], 'name'
        else:
            return '', ''

        padding = (4 - len(payload) % 4) % 4
        try:
            value = base64.urlsafe_b64decode(payload + '=' * padding).decode('utf-8')
            return value, kind
        except ValueError:    # binascii.Error, UnicodeDecodeError and non-ASCII input
            return '', ''

    @classmethod
    def decode_playlist(cls, subsonic_id: str) -> str | None:
        """Decode a playlist ID back to its raw stem (dir id, suffix, and optional owner)."""
        sid = str(subsonic_id)
        if not sid.startswith(cls._PLY_ID_PREF):
            return None
        return sid[len(cls._PLY_ID_PREF):]

    @classmethod
    def encode_artist(cls, name_or_mbid: Any, is_mbid: bool = True, *, joint_credit: bool = False) -> str:
        """
        Mint an artist ID from a mbid or a plain name.

            joint_credit: hash the full credit text to avoid colliding with the first artists's solo MBid
        """

        if joint_credit:
            digest = hashlib.sha1(str(name_or_mbid).encode('utf-8')).hexdigest()[:16]
            return f"{cls._ART_HASH_PREF}{digest}"

        encoded = base64.urlsafe_b64encode(str(name_or_mbid).encode('utf-8')).rstrip(b'=').decode('utf-8')
        prefix = cls._ART_MBID_PREF if is_mbid else cls._ART_NAME_PREF
        return f"{prefix}{encoded}"

    @staticmethod
    def is_joint_credit(multi_value: Optional[str], single_name: str = '') -> bool:
        """Whether a beets *artists (multi-value) field represents more than one credited artist."""
        return len(split_beets_multi(multi_value or single_name)) > 1

    @classmethod
    def encode_album(cls,
            beets_id: Any,
            mb_albumid: Optional[str] = None,
            albumartist: Optional[str] = None,
            album: Optional[str] = None,
        ) -> str:
        """
        Mint the album ID for a beets album: prefers mb_albumid (the MusicBrainz release
        id), falls back to a hash of albumartist+album title, or to the raw beets row ID
        if neither is available (unstable across reimports).

        beets_id/mb_albumid/albumartist/album can come from either an album row or a
        song's own version of the same fields (items store mb_albumid/albumartist/album directly too).
        """
        mbid = str(mb_albumid or '').strip()
        if mbid:
            encoded = base64.urlsafe_b64encode(mbid.encode('utf-8')).rstrip(b'=').decode('utf-8')
            return f"{cls._ALB_MBID_PREF}{encoded}"

        key = f"{albumartist or ''}\x1f{album or ''}"
        if key != '\x1f':
            digest = hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]
            return f"{cls._ALB_HASH_PREF}{digest}"

        return f"{cls._ALB_ID_PREF}{beets_id or 0}"

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
