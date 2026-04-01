import binascii
import string
from typing import TYPE_CHECKING, Union, Optional, Dict, List, Tuple, Any, Sequence
import threading
import os
import shutil
import platform
import importlib
from functools import lru_cache
from datetime import datetime, timedelta, timezone
import re
import json
import base64
import mimetypes
import unicodedata
from urllib.parse import unquote
import xml.etree.ElementTree as ET
# from xml.dom import minidom
import requests
import requests_cache
import urllib.parse
import flask

from beets import library

if TYPE_CHECKING:
    from beets.dbcore.db import Transaction

from beetsplug.beetstreamnext import app


API_VERSION = '1.16.1'
BEETSTREAMNEXT_VERSION = '1.6.0-dev'

# Prefixes for BeetstreamNext's internal IDs
ART_ID_PREF   = 'ar-'
ART_MBID_PREF = 'ar-m-'   # ar-m-<base64url(mbid)>  preferred if mbid is known
ART_NAME_PREF = 'ar-n-'   # ar-n-<base64url(name)>  fallback
ALB_ID_PREF = 'al-'
SNG_ID_PREF = 'sg-'
PLY_ID_PREF = 'pl-'


FFMPEG_BIN = shutil.which("ffmpeg") is not None
FFMPEG_PYTHON = importlib.util.find_spec("ffmpeg") is not None

if FFMPEG_PYTHON:
    import ffmpeg
elif FFMPEG_BIN:
    import subprocess
    ffmpeg = None
try:
    import wikipediaapi
    WIKI_API = True
except ImportError:
    WIKI_API = False


_BEETS_MULTI_DELIM = '\\\u2400'     # what's used in beets' db to separate multiple artists, multiple genres etc
_MORE_GENRES_DELIM = re.compile('|'.join([';', ',', '/', '\\|', '\u2400', '\\', '\x00']))

_ASCII_TRANSLATE_TABLE = {
    ord('\u2010'): '-', ord('\u2011'): '-', ord('\u2012'): '-',
    ord('\u2013'): '-', ord('\u2014'): '-', ord('\u2015'): '-',
    ord('\u2212'): '-', ord('\u2018'): "'", ord('\u2019'): "'",
    ord('\u201a'): "'", ord('\u201b'): "'", ord('\u201c'): '"',
    ord('\u201d'): '"', ord('\u201e'): '"', ord('\u201f'): '"',
    ord('\u00a0'): ' ', ord('\u2000'): ' ', ord('\u2001'): ' ',
    ord('\u2002'): ' ', ord('\u2003'): ' ', ord('\u2004'): ' ',
    ord('\u2005'): ' ', ord('\u2006'): ' ', ord('\u2007'): ' ',
    ord('\u2008'): ' ', ord('\u2009'): ' ', ord('\u200a'): ' ',
    ord('\u202f'): ' ', ord('\u2026'): '...',
}

http_session = requests_cache.CachedSession(
    str(app.config['HTTP_CACHE_PATH']),
    backend='sqlite',
    expire_after=timedelta(days=30),
    allowable_codes=[200],
    stale_if_error=True         # serve expired cached version if remote server goes down
)


##
# Main response and error payloads

def subsonic_response(data: Optional[Dict] = None, resp_fmt: str = 'xml', failed: bool = False):
    """
    Wraps json-like dict with the subsonic response data and
    outputs the appropriate format (json or xml).
    """
    data = data or {}

    if resp_fmt.startswith('json'):
        wrapped = {
            'subsonic-response': {
                'status': 'failed' if failed else 'ok',
                'version': API_VERSION,
                'type': 'BeetstreamNext',
                'serverVersion': BEETSTREAMNEXT_VERSION,
                'openSubsonic': True,
                **data
            }
        }
        return jsonpify(resp_fmt, wrapped)

    else:
        root = dict_to_xml("subsonic-response", data)
        root.set("xmlns", "http://subsonic.org/restapi")
        root.set("status", 'failed' if failed else 'ok')
        root.set("version", API_VERSION)
        root.set("type", 'BeetstreamNext')
        root.set("serverVersion", BEETSTREAMNEXT_VERSION)
        root.set("openSubsonic", 'true')

        xml_bytes = ET.tostring(root, encoding='UTF-8', method='xml', xml_declaration=True)
        # xml_bytes = minidom.parseString(xml_bytes).toprettyxml(encoding='UTF-8')
        xml_str = xml_bytes.decode('UTF-8')

        return flask.Response(xml_str, mimetype="text/xml")


def subsonic_error(code: int = 0, message: str = '', resp_fmt: str = 'xml'):

    subsonic_errors = {
        0:  'A generic error.',
        10: 'Required parameter is missing.',
        20: 'Incompatible Subsonic REST protocol version. Client must upgrade.',
        30: 'Incompatible Subsonic REST protocol version. Server must upgrade.',
        40: 'Wrong username or password.',
        41: 'Token authentication not supported.',
        42: 'Provided authentication mechanism not supported.',
        43: 'Multiple conflicting authentication mechanisms provided.',
        44: 'Invalid API key.',
        50: 'User is not authorized for the given operation.',
        70: 'The requested data was not found.'
    }

    err_payload = {
        'error': {
            'code': code,
            'message': message if message else subsonic_errors[code],
            # 'helpUrl': ''
        }
    }

    return subsonic_response(err_payload, resp_fmt=resp_fmt, failed=True)


def imageart_url(item_id: str, size: Optional[int] = None) -> str:
    if not item_id:
        return ''

    # check if the base URL is already built for the current request, if not, build it
    base_url = getattr(flask.g, '_art_base_url', None)
    if not base_url:
        params = {
            k: flask.request.values.get(k)
            for k in ['u', 's', 't', 'p', 'apiKey', 'c', 'v'] if flask.request.values.get(k)
        }
        base_url = flask.url_for('endpoint_get_cover_art', _external=True, **params)
        flask.g._art_base_url = base_url

    sep = '&' if '?' in base_url else '?'
    url = f"{base_url}{sep}id={item_id}"
    if size:
        url += f"&size={size}"
    return url


##
# BeetstreamNext internal IDs mappers

def beets_to_sub_artist(name_or_mbid: str, is_mbid: bool = True) -> str:
    encoded = base64.urlsafe_b64encode(str(name_or_mbid).encode('utf-8')).rstrip(b'=').decode('utf-8')
    prefix = ART_MBID_PREF if is_mbid else ART_NAME_PREF
    return f"{prefix}{encoded}"


def sub_to_beets_artist(subsonic_artist_id: str) -> tuple[str, bool]:
    sid = str(subsonic_artist_id)

    if sid.startswith(ART_MBID_PREF):
        payload = sid[len(ART_MBID_PREF):]
        is_mbid = True
    elif sid.startswith(ART_NAME_PREF):
        payload = sid[len(ART_NAME_PREF):]
        is_mbid = False
    else:
        return '', False

    padding = (4 - len(payload) % 4) % 4
    try:
        value = base64.urlsafe_b64decode(payload + '=' * padding).decode('utf-8')
        return value, is_mbid
    except (binascii.Error, UnicodeDecodeError):
        return '', False

def beets_to_sub_album(beet_album_id):
    return f'{ALB_ID_PREF}{beet_album_id}'

def sub_to_beets_album(subsonic_album_id):
    return int(str(subsonic_album_id)[len(ALB_ID_PREF):])

def beets_to_sub_song(beet_song_id):
    return f'{SNG_ID_PREF}{beet_song_id}'

def sub_to_beets_song(subsonic_song_id):
    return int(str(subsonic_song_id)[len(SNG_ID_PREF):])


##
# Caching user data in g

# TODO: need to figure out something better than loading all likes at once

def cached_user_likes():
    if 'liked' not in flask.g:
        from beetsplug.beetstreamnext.users import load_user_likes
        flask.g.liked = load_user_likes(flask.g.username)
    return flask.g.liked


def cached_user_ratings():
    if 'ratings' not in flask.g:
        from beetsplug.beetstreamnext.users import load_user_ratings
        flask.g.ratings = load_user_ratings(flask.g.username)
    return flask.g.ratings


def cached_user_play_stats():
    if 'play_stats' not in flask.g:
        from beetsplug.beetstreamnext.users import load_user_play_stats
        flask.g.play_stats = load_user_play_stats(flask.g.username)
    return flask.g.play_stats


def resolve_artist(req_id: str) -> Optional[Tuple[str, str]]:
    """
    Returns (name, mbid) for an artist, from any subsonic ID (artist, album, or song)
    (or None if the ID can't be resolved).
    """
    if req_id.startswith(SNG_ID_PREF):
        item = flask.g.lib.get_item(sub_to_beets_song(req_id))
        if not item:
            return None

        return item.get('albumartist', ''), item.get('mb_artistid', '')

    if req_id.startswith(ALB_ID_PREF):
        album = flask.g.lib.get_album(sub_to_beets_album(req_id))
        if not album:
            return None

        return album.get('albumartist', ''), album.get('mb_artistid', '')

    # Artist ID (or name as fallback)
    if req_id.startswith(ART_ID_PREF):
        value, is_mbid = sub_to_beets_artist(req_id)
    else:
        value, is_mbid = req_id, False

    if is_mbid:
        with flask.g.lib.transaction() as tx:
            rows = tx.query(
                """
                SELECT albumartist 
                FROM albums 
                WHERE mb_albumartistid = ? 
                LIMIT 1
                """, (value,)
            )
        artist_name = rows[0][0] if rows else ''
        if not artist_name:
            return None

        return artist_name, value   # value is the mbid

    else:
        artist_name = value
        with flask.g.lib.transaction() as tx:
            rows = tx.query(
                """
                SELECT mb_artistid 
                FROM items 
                WHERE albumartist LIKE ? 
                LIMIT 1
                """, (artist_name,)
            )
        if not rows:
            return None

        return artist_name, rows[0][0] or ''


##
# Mapping functions to translate Beets to OpenSubsonic dict-like structures
# TODO - Support multiartists lists!!! See https://opensubsonic.netlify.app/docs/responses/child/

def standardise_datadict(obj: Union[dict, library.LibModel, any]) -> dict:
    """Standardise input (Beets Item/Album or sqlite3.Row) into a dict."""
    if isinstance(obj, library.LibModel):
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


def map_media(beets_object: Union[Dict, library.LibModel]) -> Dict:

    data = standardise_datadict(beets_object)

    artist_name = data.get('albumartist') or data.get('artist') or ''
    artist_mbid = data.get('mb_albumartistid') or data.get('mb_artistid') or ''
    raw_genres = f"{data.get('genres') or ''};{data.get('genre') or ''}"
    formatted_genres = genres_formatter(raw_genres)

    main_genre = formatted_genres[0] if formatted_genres else ''
    genres_list = [{'name': g} for g in formatted_genres]

    if artist_mbid:
        artist_id = beets_to_sub_artist(artist_mbid)
    else:
        artist_id = beets_to_sub_artist(artist_name, is_mbid=False)

    subsonic_media = {
        'artist': artist_name,
        'artistId': artist_id,
        'displayArtist': artist_name,
        'displayAlbumArtist': artist_name,
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
    return subsonic_media


def map_album(album_object: Union[Dict, library.Album], include_songs: bool = True, song_counts: Optional[Dict] = None) -> Dict:

    data = standardise_datadict(album_object)

    beets_album_id = data.get('id', 0)
    subsonic_album_id = beets_to_sub_album(beets_album_id)
    album_name = data.get('album', '')

    subsonic_album = map_media(data)

    album_specific = {
        'id': subsonic_album_id,
        'musicBrainzId': data.get('mb_albumid') or '',
        'name': album_name,
        'sortName': album_name,
        # 'version': 'Deluxe Edition', # TODO: items table has 'media' that contains "Vinyl", "CD"< "Digital Media", etc
                        # TODO: also Musicbrainz puts stuff like "special collector's edition" in 'disambiguation'
        'coverArt': subsonic_album_id,
        'userRating': cached_user_ratings().get(subsonic_album_id, 0),
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
            count, duration = rows[0][:2] if rows else (0, 0)
            subsonic_album['songCount'] = count
            subsonic_album['duration'] = round(duration)


    if include_songs:
        # Need song details
        songs = list(flask.g.lib.items(f'album_id:{beets_album_id}'))

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
                app.logger.debug(f"Filesize prefetch failed: {e}")

        songs.sort(key=lambda s: (s.get('disc', 1), s.get('track', 1)))
        subsonic_album['song'] = [map_song(s, prefetched_sizes=song_filesizes) for s in songs]

    # Average rating
    if subsonic_album.get('song'):
        ratings = [s.get('userRating', 0) for s in subsonic_album['song'] if s.get('userRating', 0)]
        subsonic_album['averageRating'] = sum(ratings) / len(ratings) if ratings else 0
    else:
        subsonic_album['averageRating'] = album_specific['userRating']

    # Starred status
    liked_at = cached_user_likes().get(subsonic_album_id)
    if liked_at:
        subsonic_album['starred'] = timestamp_to_iso(liked_at)

    return subsonic_album


def map_song(song_object: Union[Dict, library.Item], prefetched_sizes: Optional[Dict[str, int]] = None) -> Dict:

    data = standardise_datadict(song_object)

    beets_song_id = data.get('id', 0)
    song_id = beets_to_sub_song(beets_song_id)
    song_title = data.get('title') or ''

    subsonic_song = map_media(data)

    song_filepath = os.fsdecode(data.get('path', b''))
    album_id = beets_to_sub_album(data.get('album_id', 0))

    song_specific = {
        'id': song_id,
        'musicBrainzId': data.get('mb_releasetrackid') or data.get('mb_trackid') or '',
        'isrc': data.get('isrc') or '',
        'name': song_title,
        'sortName': song_title,
        'albumId': album_id,
        'coverArt': album_id or song_id,
        'language': data.get('language') or '',
        'path': song_filepath,
        'userRating': cached_user_ratings().get(song_id, 0),
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

    # TODO: lyricist, composer, etc

    track_nb = data.get('track')
    if track_nb:
        subsonic_song['track'] = track_nb

    # subsonic_song['replayGain'] = {
    #         'trackGain': (song.get('rg_track_gain') or 0) or ((song.get('r128_track_gain') or 107) - 107),
    #         'albumGain': (song.get('rg_album_gain') or 0) or ((song.get('r128_album_gain') or 107) - 107),
    #         'trackPeak': song.get('rg_track_peak', 0),
    #         'albumPeak': song.get('rg_album_peak', 0)
    # }

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

    stats = cached_user_play_stats().get(beets_song_id)
    if stats:
        subsonic_song['playCount'] = stats['play_count']
        if stats['last_played']:
            subsonic_song['played'] = timestamp_to_iso(stats['last_played'])

    liked_at = cached_user_likes().get(subsonic_song['id'])
    if liked_at:
        subsonic_song['starred'] = timestamp_to_iso(liked_at)

    return subsonic_song


def map_artist(artist_name: str, with_albums: bool = True, prefetched: Optional[Dict] = None) -> Dict:

    # Priority: prefetched -> album query (when with_albums) -> standalone db query
    mbid = ''
    albums = None

    if prefetched and artist_name in prefetched:
        mbid = prefetched[artist_name].get('mbid') or ''

    elif with_albums:
        from beetsplug.beetstreamnext.albums import get_song_counts

        albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))
        if albums:
            mbid = albums[0].get('mb_albumartistid', '') or ''

    else:
        with flask.g.lib.transaction() as tx:
            rows = tx.query(
                """
                SELECT COUNT(*), mb_albumartistid
                FROM albums
                WHERE albumartist = ?
                GROUP BY albumartist
                """, (artist_name,)
            )
        if rows:
            mbid = rows[0][1] or ''

    if mbid:
        subsonic_artist_id = beets_to_sub_artist(mbid)
    else:
        subsonic_artist_id = beets_to_sub_artist(artist_name, is_mbid=False)

    subsonic_artist = {
        'id': subsonic_artist_id,
        'name': artist_name,
        'sortName': artist_name,
        'title': artist_name,
        'coverArt': subsonic_artist_id,
        'userRating': cached_user_ratings().get(subsonic_artist_id, 0),
        'artistImageUrl': imageart_url(subsonic_artist_id),

        # "roles": [
        #     "artist",
        #     "albumartist",
        #     "composer"
        # ],

        # This is only needed when part of a Child response
        'mediaType': 'artist'
    }

    if with_albums:
        from beetsplug.beetstreamnext.albums import get_song_counts

        if albums is None:  # already fetched above if not prefetched
            albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))

            if albums and not mbid:
                mbid = albums[0].get('mb_albumartistid', '') or ''

        subsonic_artist['albumCount'] = len(albums)
        subsonic_artist['musicBrainzId'] = mbid

        song_counts = get_song_counts(albums)
        subsonic_artist['album'] = [map_album(alb, include_songs=False, song_counts=song_counts) for alb in albums]

    else:
        if prefetched and artist_name in prefetched:
            subsonic_artist['albumCount'] = prefetched[artist_name]['album_count']
            subsonic_artist['musicBrainzId'] = mbid
        else:
            if rows:
                subsonic_artist['albumCount'] = rows[0][0]
                subsonic_artist['musicBrainzId'] = mbid
            else:
                subsonic_artist['albumCount'] = 0

    liked_at = cached_user_likes().get(subsonic_artist_id)
    if liked_at:
        subsonic_artist['starred'] = timestamp_to_iso(liked_at)

    return subsonic_artist


def map_playlist(playlist, include_songs=False):
    subsonic_playlist = {
        'id': playlist.id,
        'name': playlist.name,
        'songCount': playlist.song_count,
        'duration': playlist.duration,
        'created': timestamp_to_iso(playlist.ctime),
        'changed': timestamp_to_iso(playlist.mtime),

        # 'owner': 'userA',     # TODO
        # 'public': True,
    }
    if include_songs and playlist.songs:
        subsonic_playlist['entry'] = playlist.songs

    return subsonic_playlist


## Requests format conversions

def dict_to_xml(tag: str, data):
    """
    Converts a json-like dict to an XML tree.
    Simple values are mapped as attributes unless the attribute name already exists
    or the key is "value", in which case they become text or child elements.
    """
    elem = ET.Element(tag)

    def _fmt(v):
        return str(v).lower() if isinstance(v, bool) else str(v)

    def _add_node(parent, key, val):
        """Decide if a simple value should be an attribute or a child/text."""
        if key == "value":
            parent.text = _fmt(val)
        elif key in parent.attrib:
            child = ET.Element(key)
            child.text = _fmt(val)
            parent.append(child)
        else:
            parent.set(key, _fmt(val))

    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, (dict, list)):
                        elem.append(dict_to_xml(key, item))
                    else:
                        _add_node(elem, key, item)
            elif isinstance(val, dict):
                elem.append(dict_to_xml(key, val))
            else:
                _add_node(elem, key, val)

    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                elem.append(dict_to_xml(tag, item))
            else:
                _add_node(elem, tag, item)
    else:
        elem.text = _fmt(data)

    return elem


def jsonpify(format: str, data: dict):
    if format == 'jsonp':
        callback = flask.request.values.get("callback")
        return flask.Response(f"{callback}({json.dumps(data)});", mimetype='application/javascript')
    else:
        return flask.jsonify(data)


##
# Text utilities

def remove_accents(text: Any):
    if not text:
        return ''
    return ''.join(c for c in unicodedata.normalize('NFD', str(text)) if unicodedata.category(c) != 'Mn')


def split_beets_multi(stringlist: Union[Sequence[Any], str]) -> List[str]:
    """Split a beets multi-value field."""
    if not stringlist:
        return []

    if not isinstance(stringlist, str) and isinstance(stringlist, Sequence):
        # re-join if it's a sequence
        stringlist = _BEETS_MULTI_DELIM.join(stringlist)

    splitted = str(stringlist).split(_BEETS_MULTI_DELIM)
    return [s.strip('\\\u2400') for s in splitted if s]


def customstrip(value: Any, punctuation: bool = False) -> str:
    if not value:
        return ''
    if isinstance(value, bytes):
        try:
            s = value.decode('utf-8')
        except UnicodeDecodeError:
            return ''
    else:
        s = str(value)
    to_strip = string.whitespace + '\v\f\x00'
    if punctuation:
        to_strip += string.punctuation

    return s.strip(to_strip)


def standard_ascii(text: Any) -> str:
    """Replace fancy unicode characters by standard ASCII equivalents."""
    if not text:
        return ''
    text = unicodedata.normalize('NFC', str(text))
    return text.translate(_ASCII_TRANSLATE_TABLE).strip()


def trim_text(text, char_limit=300):
    if len(text) <= char_limit:
        return text

    snippet = text[:char_limit]
    period_index = text.find(".", char_limit)

    if period_index != -1:
        snippet = text[:period_index + 1]

    return snippet


##
# Various parsers / converters / formatters

def safe_str(val: Any) -> str:
    if val is None:
        return ''
    s = unquote(str(val))
    s = unicodedata.normalize('NFC', s)
    s = standard_ascii(s)
    return customstrip(s)


def api_bool(val: Any) -> bool:
    if val is None:
        return False
    return safe_str(val).lower() not in ('false', '0', 'no', 'none', 'null', '')


def timestamp_to_iso(timestamp) -> str:
    if not timestamp or timestamp == 0:
        return ''
    try:
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return ''


@lru_cache(maxsize=1024)
def genres_formatter(genres: Optional[str]) -> Tuple[str, ...]:
    """Additional cleaning for common genres formatting issues."""
    if not genres:
        return ()

    raw_list = split_beets_multi(genres)
    separated = _MORE_GENRES_DELIM.split(';'.join(raw_list))

    cleaned = []
    for g in separated:
        tag = standard_ascii(g).title()

        tag = (tag.replace('Post ', 'Post-')
               .replace('Prog ', 'Progressive ')
               .replace('Rnb', 'R&B')
               .replace("R'N'B", 'R&B')
               .replace("R 'N' B", 'R&B')
               .replace('Rock & ', 'Rock and ')
               .replace("Rock'N'", 'Rock and')
               .replace("Rock 'N'", 'Rock and')
               .replace('.', ' '))

        final_tag = customstrip(tag, punctuation=True)
        final_tag = remove_accents(final_tag)
        if final_tag and final_tag not in cleaned:
            cleaned.append(final_tag)

    return tuple(cleaned)


##
# File access and format detection utilities

def creation_date(filepath):
    """Get a file's creation date."""

    if platform.system() == 'Windows':
        return os.path.getctime(filepath)

    stat = os.stat(filepath)

    if platform.system() == 'Darwin':
        return stat.st_birthtime

    # Linux: fall back to mtime
    return getattr(stat, 'st_birthtime', stat.st_mtime)


def get_mimetype(path):

    if not path:
        return 'application/octet-stream'

    path = os.fsdecode(path)
    if '.' not in path or path.startswith('.'):
        # Assume the passed arg is just an extension
        path = f'file.{path}'

    mimetype_fallback = {
        '.aac': 'audio/aac',
        '.flac': 'audio/flac',
        '.mp3': 'audio/mpeg',
        '.mp4': 'audio/mp4',
        '.m4a': 'audio/mp4',
        '.ogg': 'audio/ogg',
        '.opus': 'audio/opus',
        None: 'application/octet-stream'
    }
    return mimetypes.guess_type(path)[0] or mimetype_fallback.get(path.rsplit('.', 1)[-1], 'application/octet-stream')


##
# External APIs querying

def query_musicbrainz(mbid: str, type: str):

    types_mb = {'track': 'recording', 'album': 'release', 'artist': 'artist'}
    endpoint = f'https://musicbrainz.org/ws/2/{types_mb[type]}/{mbid}'

    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'}
    params = {'fmt': 'json'}

    if types_mb[type] == 'artist':
        params['inc'] = 'annotation'

    try:
        response = http_session.get(endpoint, headers=headers, params=params, timeout=8)
        if response.from_cache:
            app.logger.debug(f"Cache hit for MusicBrainz: {mbid}")
        return response.json() if response.ok else {}

    except requests.exceptions.RequestException:
        return {}


def query_deezer(artist: Optional[str] = None, album: Optional[str] = None) -> Dict:

    if artist:
        artist = urllib.parse.quote_plus(artist)
    if album:
        album = urllib.parse.quote_plus(album)

    if not artist and not album:
        return {}

    base_search = 'https://api.deezer.com/search/'

    if artist and album:
        search_endpoint = base_search + f'?q=artist:"{artist}" album:"{album}"'
    elif artist:
        search_endpoint = base_search + f'artist?q={artist}'
    elif album:
        search_endpoint = base_search + f'album?q={album}'

    search_endpoint += '&limit=1&index=0'
    # TODO: Actually Deezer's API sometimes return a duplicate (wrong) entry with same artist name.
    #   Maybe fix: use the 'nb_fan' entry to disambiguate?
    #   Example 'Mariah Carey' axists as nb_fan: 58 and nb_fan: 3404526, obviously the real one is the 2nd

    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'}

    try:
        response = http_session.get(search_endpoint, headers=headers, timeout=8)
        if response.from_cache:
            app.logger.debug(f"Cache hit for Deezer: {artist}")
        if response.ok:
            data = response.json().get('data', {})
            if data:
                return data[0]
    except requests.exceptions.RequestException:
        return {}

    return {}


def query_lastfm(q: str, type: str, method: str = 'info', is_mbid=True) -> Dict:

    if not app.config['lastfm_api_key']:
        return {}

    endpoint = 'https://ws.audioscrobbler.com/2.0/'

    params = {
        'format': 'json',
        'method': f'{type}.get{method.title()}',
        'api_key': app.config['lastfm_api_key'],
        }

    if is_mbid:
        q = q.replace(' ', '+')
        params['mbid'] = q
    elif q and type != 'user':
        params[type] = q

    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'}
    try:
        response = http_session.get(endpoint, headers=headers, params=params, timeout=15) # lastfm is very slow...
        if response.from_cache:
            app.logger.debug(f"Cache hit for Last.fm: {q}")
        return response.json() if response.ok else {}

    except requests.exceptions.RequestException:
        return {}


@lru_cache(maxsize=512)
def query_wikipedia(q: str) -> Optional[str]:
    if not WIKI_API:
        return None

    q = standard_ascii(q)
    if not q:
        return None

    user_agent = f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'
    wiki = wikipediaapi.Wikipedia(user_agent=user_agent, language='en', timeout=8)
    page = wiki.page(q)

    if page.exists():
        return page.summary

    return None


##
# Beets' database access utilities

_schema_cache: Dict[str, Any] = {}
_schema_lock = threading.Lock()

_beets_table_names = frozenset(['items', 'albums'])


def get_beets_schema(table_name: str = 'items') -> List[str]:
    """Returns column names for the beets db, invalidating the cache if the beets db has changed."""

    if table_name not in _beets_table_names:
        raise AttributeError(f"Table {table_name} does not exist in Beets' db.")

    lib_path = flask.g.lib.path
    current_mtime = os.path.getmtime(os.fsdecode(lib_path))
    cache_key = f'schema_{table_name}'

    with _schema_lock:
        if _schema_cache.get('_mtime') != current_mtime:
            _schema_cache.clear()
            _schema_cache['_mtime'] = current_mtime

        if cache_key in _schema_cache:
            return _schema_cache[cache_key]

    # Query outside lock to avoid holding during IO
    with flask.g.lib.transaction() as tx:
        # SQLite PRAGMA doesnt support bound parameters for table name
        cursor = tx.query(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor]

    with _schema_lock:
        # cache only if mtime hasn't changed while querying
        if _schema_cache.get('_mtime') == current_mtime:
            _schema_cache[cache_key] = columns

    return columns


def chunked_query(tx: 'Transaction', query_template: str, values: List[str], chunk_size=900):
    """
    tx: The beets transaction or sqlite connection
    query_template: SQL string with a '{q}' placeholder for the IN clause
    values: The list of values to query
    """
    results = []
    for i in range(0, len(values), chunk_size):
        chunk = values[i: i + chunk_size]
        question_marks = ','.join(['?'] * len(chunk))

        sql = query_template.replace('{q}', question_marks)

        chunk_results = list(tx.query(sql, chunk))
        results.extend(chunk_results)
    return results