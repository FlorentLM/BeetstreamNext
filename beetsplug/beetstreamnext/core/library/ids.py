from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.utils.text import split_beets_multi, sha1_hex, validate_mbid
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
    _SNG_HASH_PREF = 'sg-h-'    # sg-h-<hash of path relative to music_root>

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
    def decode_song(cls, subsonic_id: str) -> tuple[Any, str] | tuple[None, None]:
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
    def decode_album(cls, subsonic_id: str) -> tuple[Any, str] | tuple[None, None]:
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
    def decode_artist(cls, subsonic_id: str) -> tuple[str, str]:
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
    def encode_artist(cls, name_or_mbid: Any, is_mbid: bool | None = None, *, joint_credit: bool = False) -> str:
        """
        Mint an artist ID from a mbid or a plain name.

            is_mbid: whether name_or_mbid is an MBID or a plain name. Required (unless joint_credit).
            joint_credit: hash the full credit text to avoid colliding with the first artists's solo MBid
        """

        if joint_credit:
            digest = sha1_hex(str(name_or_mbid))[:16]
            return f"{cls._ART_HASH_PREF}{digest}"

        if is_mbid is None:
            raise TypeError("encode_artist() needs is_mbid=True/False (unless joint_credit=True)")

        encoded = base64.urlsafe_b64encode(str(name_or_mbid).encode('utf-8')).rstrip(b'=').decode('utf-8')
        prefix = cls._ART_MBID_PREF if is_mbid else cls._ART_NAME_PREF
        return f"{prefix}{encoded}"

    @classmethod
    def artist_id(cls, name: str, mbid: Any = None, multi: str | None = None, *, joint: bool | None = None) -> str:
        """
        ID of an artist credit: hash of the credit text for joint credits, otherwise the MBID, or the name.
            mbid: the MBID (Spotify/Deezer values ignored)
            multi: the multi-value field (albumartists / artists)
            joint: if the caller already knows (skips detection here)
        """
        if joint is None:
            joint = cls.is_joint_credit(multi, name)

        if joint:
            return cls.encode_artist(name, joint_credit=True)

        mbid = validate_mbid(mbid)
        return cls.encode_artist(mbid or name, is_mbid=bool(mbid))

    @staticmethod
    def main_artist_of(data: dict) -> tuple[str, str, str]:
        """
        (name, mbid, multi-value names) of the main artist for a song or album
        (the album artist, or fallback to the track artist)
        """
        albumartist = data.get('albumartist') or ''
        artist = data.get('artist') or ''

        if not albumartist:
            return artist, validate_mbid(data.get('mb_artistid')), data.get('artists') or ''

        mbid = validate_mbid(data.get('mb_albumartistid'))
        multi = data.get('albumartists') or ''

        if artist == albumartist:
            mbid = mbid or validate_mbid(data.get('mb_artistid'))
            multi = multi or data.get('artists') or ''

        return albumartist, mbid, multi

    @staticmethod
    def is_joint_credit(multi_value: str | None, single_name: str = '') -> bool:
        """Whether a beets *artists (multi-value) field represents more than one credited artist."""
        return len(split_beets_multi(multi_value or single_name)) > 1

    @classmethod
    def credit_pairs(cls,
            name: str,
            mbid: Any,
            names_raw: str | None,
            mbids_raw: str | None,
            *, joint: bool | None = None
        ) -> list[tuple[str, str]]:
        """
        (name, MBID or '') of every artist of a credit.
        Solo: use single-value fields, or a 1-entry multi-value MBID
        Joint: members paired by position in the multi-value fields
        """
        if joint is None:
            joint = cls.is_joint_credit(names_raw, name)

        mbids = split_beets_multi(mbids_raw or '')

        if not joint:
            if not name:
                return []
            return [(name, validate_mbid(mbid) or (validate_mbid(mbids[0]) if len(mbids) == 1 else ''))]

        names = split_beets_multi(names_raw or '')
        aligned = len(mbids) == len(names)
        return [(n, validate_mbid(mbids[i]) if aligned else '') for i, n in enumerate(names) if n]

    @classmethod
    def joint_credit_keys(cls, name: str, names_raw: str | None, mbids_raw: str | None) -> list[str]:
        """
        Keys of a joint credit (set of all members' mbids then the credit text).
        """
        keys = [cls.encode_artist(name, joint_credit=True)]

        mbids = [validate_mbid(m) for m in split_beets_multi(mbids_raw or '')]
        if mbids and all(mbids) and len(mbids) == len(split_beets_multi(names_raw or '')):
            keys.insert(0, cls.encode_artist('+'.join(sorted(mbids)), joint_credit=True))

        return keys

    @classmethod
    def encode_album(cls,
            beets_id: Any,
            mb_albumid: str | None = None,
            albumartist: str | None = None,
            album: str | None = None,
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
            digest = sha1_hex(key)[:16]
            return f"{cls._ALB_HASH_PREF}{digest}"

        return f"{cls._ALB_ID_PREF}{beets_id or 0}"

    @classmethod
    def encode_song(cls, song: dict, _music_root: bytes | str | Path | None = None) -> str:
        """
        Mint the song ID for a beets item: prefers whatever external database ID beets
        recorded (MusicBrainz, Deezer, Spotify etc... beets always writes them in the mb_* field).

        Falls back to a hash of the item's path (relative to the root directory), or to the raw beets
        row ID if neither is available (but this one is unstable across reimports).

        Note: _music_root is only necessary for the db migration callsite, it's called
            before app.config['music_root'] is set
        """
        mbid = str(song.get('mb_releasetrackid') or song.get('mb_trackid') or '').strip()
        digest = '' if mbid else path_hash(song.get('path'), _music_root or app.config['music_root'])
        return cls.song_id(mbid, digest, song.get('id', 0))

    @classmethod
    def song_id(cls, mbid: str, digest: str, beets_id: Any) -> str:

        mbid = str(mbid or '').strip()
        if mbid:
            encoded = base64.urlsafe_b64encode(mbid.encode('utf-8')).rstrip(b'=').decode('utf-8')
            return f"{cls._SNG_MBID_PREF}{encoded}"
        if digest:
            return f"{cls._SNG_HASH_PREF}{digest}"
        return f"{cls._SNG_ID_PREF}{beets_id}"

    @classmethod
    def encode_playlist(cls, db_id: int) -> str:
        """
        Mint a playlist ID from its db row ID.
        """
        return f'{cls._PLY_ID_PREF}{db_id}'

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
