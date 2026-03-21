import unicodedata
from datetime import datetime
import platform
from pathlib import Path
from typing import Union, Optional, Dict
import flask
import json
import base64
import mimetypes
import os
import re
from beets import library
import xml.etree.cElementTree as ET
from xml.dom import minidom
import shutil
import importlib
from functools import partial
import requests
import urllib.parse

from beetsplug.beetstreamnext import app


API_VERSION = '1.16.1'
BEETSTREAMNEXT_VERSION = '1.5.0-dev'

# Prefixes for BeetstreamNext's internal IDs
ART_ID_PREF = 'ar-'
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


GENRE_DELIM = re.compile('|'.join([';', ',', '/', '\\|', '\␀', '\x00']))

# BeetstreamNext internal IDs: they are sent to the client once (when it accesses endpoints such as getArtists
# or getAlbumList) and the client will then use them to access a specific item via endpoints that need an ID

def beets_to_sub_artist(beet_artist_name):
    base64_name = base64.urlsafe_b64encode(str(beet_artist_name).encode('utf-8'))
    return f"{ART_ID_PREF}{base64_name.rstrip(b'=').decode('utf-8')}"

def sub_to_beets_artist(subsonic_artist_id):
    subsonic_artist_id = str(subsonic_artist_id)[len(ART_ID_PREF):]
    padding = 4 - (len(subsonic_artist_id) % 4)
    return base64.urlsafe_b64decode(subsonic_artist_id + ('=' * padding)).decode('utf-8')

def beets_to_sub_album(beet_album_id):
    return f'{ALB_ID_PREF}{beet_album_id}'

def sub_to_beets_album(subsonic_album_id):
    return int(str(subsonic_album_id)[len(ALB_ID_PREF):])

def beets_to_sub_song(beet_song_id):
    return f'{SNG_ID_PREF}{beet_song_id}'

def sub_to_beets_song(subsonic_song_id):
    return int(str(subsonic_song_id)[len(SNG_ID_PREF):])


# Mapping functions to translate Beets to OpenSubsonic dict-like structures
# TODO - Support multiartists lists!!! See https://opensubsonic.netlify.app/docs/responses/child/

def map_media(beets_object: Union[dict, library.LibModel]):
    beets_object = dict(beets_object)

    artist_name = beets_object.get('albumartist') or ''

    raw_genres = f"{beets_object.get('genres') or ''};{beets_object.get('genre') or ''}"
    formatted_genres = genres_formatter(raw_genres)

    main_genre = formatted_genres[0] if formatted_genres else ''
    genres_list = [{'name': g} for g in formatted_genres]

    subsonic_media = {
        'artist': artist_name,
        'artistId': beets_to_sub_artist(artist_name),
        'displayArtist': artist_name,
        'displayAlbumArtist': artist_name,
        'album': beets_object.get('album') or '',
        'year': beets_object.get('year') or 0,
        'genre': main_genre,
        'genres': genres_list,
        'created': timestamp_to_iso(beets_object.get('added')),
        'originalReleaseDate': {
            'year': beets_object.get('original_year') or beets_object.get('year') or 0,
            'month': beets_object.get('original_month') or beets_object.get('month') or 0,
            'day': beets_object.get('original_day') or beets_object.get('day') or 0
        },
        'releaseDate': {
            'year': beets_object.get('year') or 0,
            'month': beets_object.get('month') or 0,
            'day': beets_object.get('day') or 0
        },
    }
    return subsonic_media


def map_album(album_object: Union[dict, library.Album], with_songs=True) -> dict:
    album = dict(album_object)

    subsonic_album = map_media(album)

    beets_album_id = album.get('id', 0)
    subsonic_album_id = beets_to_sub_album(beets_album_id)
    album_name = album.get('album', '')

    album_specific = {
        'id': subsonic_album_id,
        'musicBrainzId': album.get('mb_albumid', ''),
        'name': album_name,
        'sortName': album_name,
        # 'version': 'Deluxe Edition',   # TODO - Use the 'media' field maybe?
        'coverArt': subsonic_album_id,

        'userRating': flask.g.get('ratings', {}).get(subsonic_album_id, 0),

        # 'recordLabels': [{'name': l for l in stringlist_splitter(album.get('label', ''))}],
        'isCompilation': bool(album.get('comp', False)),

        # These are only needed when part of a directory response
        'isDir': True,
        'parent': subsonic_album['artistId'],

        # Title field is required for Child responses (also used in albumList or albumList2 responses)
        'title': album_name,

        # This is only needed when part of a Child response
        'mediaType': 'album'
    }
    subsonic_album.update(album_specific)

    # Add release types if possible
    release_types = album.get('albumtypes', '') or album.get('albumtype', '')
    if isinstance(release_types, str):
        subsonic_album['releaseTypes'] = stringlist_splitter(release_types)
    else:
        subsonic_album['releaseTypes'] = [r.strip().title() for r in release_types]

    # Add multi-disc info if needed
    nb_discs = album.get('disctotal', 1)
    if nb_discs > 1:
        subsonic_album["discTitles"] = [
            {'disc': d, 'title': ' - '.join(filter(None, [album.get('album', None), f'Disc {d + 1}']))}
            for d in range(nb_discs)
        ]

    # Songs should be included when part of either:
    # - an AlbumID3WithSongs response
    # - a directory response (in which case the 'song' key needs to be renamed to 'child')

    # Even if not including songs in the response, we still need to have their count and duration...
    if not isinstance(album_object, library.Album):
        # ...so in case album_object comes from a direct SQL transaction, we need to query once more
        songs = list(flask.g.lib.items(f'album_id:{beets_album_id}'))
    else:
        # ...if it is a beets.library.Album object, we already have them
        songs = list(album_object.items())

    if with_songs:
        songs.sort(key=lambda s: s.track)  # TODO - is it really necessary to sort them?
        subsonic_album['song'] = list(map(map_song, songs))

    subsonic_album['duration'] = round(sum(s.get('length', 0) for s in songs))
    subsonic_album['songCount'] = len(songs)

    # Optional field
    songs_ratings = [s.get('userRating', 0) for s in subsonic_album.get('song', []) if s.get('userRating', 0)]
    subsonic_album['averageRating'] = sum(songs_ratings) / len(songs_ratings) if songs_ratings else 0
    # (the above returns 0 when partial album which is corrrect, averageRating is only really useful on a full album)

    liked_at = flask.g.get('liked', {}).get(subsonic_album_id)
    if liked_at:
        subsonic_album['starred'] = timestamp_to_iso(liked_at)

    return subsonic_album


def map_song(song_object):
    song = dict(song_object)

    subsonic_song = map_media(song)

    song_id = beets_to_sub_song(song.get('id', 0))
    song_name = song.get('title') or ''

    filepath_beets = song.get('path')
    if isinstance(filepath_beets, bytes):
        try:
            song_filepath = filepath_beets.decode('utf-8')
        except UnicodeDecodeError:
            song_filepath = ''
    elif isinstance(filepath_beets, str):
        song_filepath = filepath_beets
    else:
        song_filepath = ''
    if song_filepath and not os.path.isfile(song_filepath):
        song_filepath = ''

    album_id = beets_to_sub_album(song.get('album_id', 0))

    song_specific = {
        'id': song_id,
        'musicBrainzId': song.get('mb_albumid') or '',
        'name': song_name,
        'sortName': song_name,
        'albumId': album_id,
        'coverArt': album_id or song_id,

        'track': song.get('track') or 1,
        'path': song_filepath,

        'played': '',
        'playCount': 0,
        'userRating': flask.g.get('ratings', {}).get(song_id, 0),

        'duration': round(song.get('length') or 0),
        'bpm': song.get('bpm') or 0,
        'bitRate': round((song.get('bitrate') or 0) / 1000),
        'bitDepth': song.get('bitdepth') or 0,
        'samplingRate': song.get('samplerate') or 0,
        'channelCount': song.get('channels') or 2,
        'discNumber': song.get('disc') or 1,
        'comment': song.get('comment') or '',

        # These are only needed when part of a directory response
        'isDir': False,
        'parent': album_id or subsonic_song['artistId'],

        # TODO - is there really no chance to have videos in beets' database?
        'isVideo': False,
        'type': 'music',

        # Title field is required for Child responses
        'title': song_name,

        # This is only needed when part of a Child response
        'mediaType': 'song'
    }
    subsonic_song.update(song_specific)

    # subsonic_song['replayGain'] = {
    #         'trackGain': (song.get('rg_track_gain') or 0) or ((song.get('r128_track_gain') or 107) - 107),
    #         'albumGain': (song.get('rg_album_gain') or 0) or ((song.get('r128_album_gain') or 107) - 107),
    #         'trackPeak': song.get('rg_track_peak', 0),
    #         'albumPeak': song.get('rg_album_peak', 0)
    # }

    suffix = (song.get('format') or '').lower()
    if not suffix and song_filepath:
        suffix = song_filepath.rsplit('.', 1)[-1].lower()
    subsonic_song['suffix'] = suffix or 'mp3'

    subsonic_song['size'] = ((os.path.getsize(subsonic_song['path']) if subsonic_song['path'] else 0)
                             or round(song.get('bitrate', 0) * song.get('length', 0) / 8))

    subsonic_song['contentType'] = get_mimetype(song_filepath or suffix)

    stats = flask.g.get('play_stats', {}).get(song.get('id'))
    if stats:
        subsonic_song['playCount'] = stats['play_count']
        if stats['last_played']:
            subsonic_song['played'] = timestamp_to_iso(stats['last_played'])

    liked_at = flask.g.get('liked', {}).get(subsonic_song['id'])
    if liked_at:
        subsonic_song['starred'] = timestamp_to_iso(liked_at)

    return subsonic_song


def map_artist(artist_name, with_albums=True):
    subsonic_artist_id = beets_to_sub_artist(artist_name)

    subsonic_artist = {
        'id': subsonic_artist_id,
        'name': artist_name,
        'sortName': artist_name,
        'title': artist_name,
        'coverArt': subsonic_artist_id,
        'userRating': flask.g.get('ratings', {}).get(subsonic_artist_id, 0),

        # "roles": [
        #     "artist",
        #     "albumartist",
        #     "composer"
        # ],

        # This is only needed when part of a Child response
        'mediaType': 'artist'
    }

    # dz_data = query_deezer(artist_name, 'artist')
    # if dz_data:
    #     subsonic_artist['artistImageUrl'] = dz_data.get('picture_big', '')

    if with_albums:
        albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))
        subsonic_artist['albumCount'] = len(albums)
        if albums:
            subsonic_artist['musicBrainzId'] = albums[0].get('mb_albumartistid', '')
        subsonic_artist['album'] = list(map(partial(map_album, with_songs=False), albums))
    else:
        with flask.g.lib.transaction() as tx:
            rows = tx.query(
                "SELECT COUNT(*), mb_albumartistid FROM albums WHERE albumartist = ? GROUP BY albumartist",
                (artist_name,)
            )
        if rows:
            subsonic_artist['albumCount'] = rows[0][0]
            subsonic_artist['musicBrainzId'] = rows[0][1] or ''
        else:
            subsonic_artist['albumCount'] = 0

    liked_at = flask.g.get('liked', {}).get(subsonic_artist_id)
    if liked_at:
        subsonic_artist['starred'] = timestamp_to_iso(liked_at)

    return subsonic_artist


def map_playlist(playlist):
    subsonic_playlist = {
        'id': playlist.id,
        'name': playlist.name,
        'songCount': len(playlist.songs),
        'duration': playlist.duration,
        'created': timestamp_to_iso(playlist.ctime),
        'changed': timestamp_to_iso(playlist.mtime),
        'entry': playlist.songs

        # 'owner': 'userA',     # TODO
        # 'public': True,
    }
    return subsonic_playlist


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
        return f"{callback}({json.dumps(data)});"
    else:
        return flask.jsonify(data)


def subsonic_response(data: dict = {}, resp_fmt: str = 'xml'):
    """Wrap any json-like dict with the subsonic response elements
     and output the appropriate 'format' (json or xml)."""

    if resp_fmt.startswith('json'):
        wrapped = {
            'subsonic-response': {
                'status': 'ok',
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
        root.set("status", 'ok')
        root.set("version", API_VERSION)
        root.set("type", 'BeetstreamNext')
        root.set("serverVersion", BEETSTREAMNEXT_VERSION)
        root.set("openSubsonic", 'true')

        xml_bytes = ET.tostring(root, encoding='UTF-8', method='xml', xml_declaration=True)
        pretty_xml = minidom.parseString(xml_bytes).toprettyxml(encoding='UTF-8')
        xml_str = pretty_xml.decode('UTF-8')

        return flask.Response(xml_str, mimetype="text/xml")


def subsonic_error(code: int = 0, message: str = '', resp_fmt: str = 'xml'):

    subsonic_errors = {
        0: 'A generic error.',
        10: 'Required parameter is missing.',
        20: 'Incompatible Subsonic REST protocol version. Client must upgrade.',
        30: 'Incompatible Subsonic REST protocol version. Server must upgrade.',
        40: 'Wrong username or password.',
        41: 'Token authentication not supported.',
        42: 'Provided authentication mechanism not supported.',
        43: 'Multiple conflicting authentication mechanisms provided.',
        44: 'Invalid API key.',
        50: 'User is not authorized for the given operation.',
        # 60: 'The trial period for the Subsonic server is over.',
        70: 'The requested data was not found.'
    }

    err_payload = {
        'error': {
            'code': code,
            'message': message if message else subsonic_errors[code],
            # 'helpUrl': ''
        }
    }

    if resp_fmt.startswith('json'):
        wrapped = {
            'subsonic-response': {
                'status': 'failed',
                'version': API_VERSION,
                'type': 'BeetstreamNext',
                'serverVersion': BEETSTREAMNEXT_VERSION,
                'openSubsonic': True,
                **err_payload
            }
        }
        return jsonpify(resp_fmt, wrapped)

    else:
        root = dict_to_xml("subsonic-response", err_payload)
        root.set("xmlns", "http://subsonic.org/restapi")
        root.set("status", 'failed')
        root.set("version", API_VERSION)
        root.set("type", 'BeetstreamNext')
        root.set("serverVersion", BEETSTREAMNEXT_VERSION)
        root.set("openSubsonic", 'true')

        xml_bytes = ET.tostring(root, encoding='UTF-8', method='xml', xml_declaration=True)
        pretty_xml = minidom.parseString(xml_bytes).toprettyxml(encoding='UTF-8')
        xml_str = pretty_xml.decode('UTF-8')

        return flask.Response(xml_str, mimetype="text/xml")


# Other utility functions

def remove_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def customstrip(value: Optional[Union[str, bytes]]) -> str:
    if not value:
        return ''
    if isinstance(value, bytes):
        try:
            value = value.decode('utf-8')
        except UnicodeDecodeError:
            return ''
    return str(value).strip(' \n\t\r\v\f\x00"\'()[]{};,\\/|')


def standard_ascii(text: str) -> str:
    """Replace fancy unicode characters by standard ASCII equivalents."""

    if not text:
        return ''

    text = unicodedata.normalize('NFC', str(text))

    replacements = {
        '\u2010': '-',
        '\u2011': '-',
        '\u2012': '-',
        '\u2013': '-',
        '\u2014': '-',
        '\u2015': '-',
        '\u2212': '-',
        '\u2018': "'",
        '\u2019': "'",
        '\u201a': "'",
        '\u201b': "'",
        '\u201c': '"',
        '\u201d': '"',
        '\u201e': '"',
        '\u201f': '"',
        '\u00a0': ' ',
        '\u2000': ' ',
        '\u2001': ' ',
        '\u2002': ' ',
        '\u2003': ' ',
        '\u2004': ' ',
        '\u2005': ' ',
        '\u2006': ' ',
        '\u2007': ' ',
        '\u2008': ' ',
        '\u2009': ' ',
        '\u200a': ' ',
        '\u202f': ' ',
        '\u2026': '...',
    }

    for unicode_char, ascii_char in replacements.items():
        text = text.replace(unicode_char, ascii_char)

    return text.strip()


def stringlist_splitter(delimiter_separated_string: str):
    if not delimiter_separated_string:
        return []
    parts = GENRE_DELIM.split(str(delimiter_separated_string))
    return [customstrip(p) for p in parts if customstrip(p)]


def timestamp_to_iso(timestamp) -> str:
    if not timestamp or timestamp == 0:
        return ''
    try:
        return datetime.fromtimestamp(float(timestamp)).isoformat()
    except (ValueError, TypeError):
        return ''


def get_mimetype(path):

    if isinstance(path, (bytes, bytearray)):
        path = path.decode('utf-8')
    elif isinstance(path, Path):
        path = path.as_posix()
    if not '.' in path:     # Assume the passed arg is just an extension
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


def get_beets_schema(table_name: str = 'items'):
    """Query beets database for column names."""
    with flask.g.lib.transaction() as tx:
        cursor = tx.query(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor]
    return columns

def genres_formatter(genres: Union[str, list, None]) -> list:
    """Additional cleaning for common genres formatting issues."""
    if not genres:
        return []

    raw_list = []
    if isinstance(genres, list):
        for item in genres:
            raw_list.extend(stringlist_splitter(item))
    else:
        raw_list = stringlist_splitter(genres)

    cleaned = []
    for g in raw_list:
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

        final_tag = customstrip(tag)
        if final_tag:
            cleaned.append(final_tag)

    return list(set(cleaned))


def creation_date(filepath):
    """
    Get a file's creation date.
    (see: https://stackoverflow.com/a/39501288/1709587)
    """
    if platform.system() == 'Windows':
        return os.path.getctime(filepath)
    elif platform.system() == 'Darwin':
        stat = os.stat(filepath)
        return stat.st_birthtime
    else:
        stat = os.stat(filepath)
        try:
            # On some Unix systems, st_birthtime is available so try it
            return stat.st_birthtime
        except AttributeError:
            try:
                # Run stat twice because it's faster and easier than parsing the %W format...
                ret = subprocess.run(['stat', '--format=%W', filepath], stdout=subprocess.PIPE)
                timestamp = ret.stdout.decode('utf-8').strip()

                # ...but we still want millisecond precision :)
                ret = subprocess.run(['stat', '--format=%w', filepath], stdout=subprocess.PIPE)
                millis = ret.stdout.decode('utf-8').rsplit('.', 1)[1].split()[0].strip()

                return float(f'{timestamp}.{millis}')
            except:
                # If that did not work, settle for last modification time
                return stat.st_mtime


def query_musicbrainz(mbid: str, type: str):

    types_mb = {'track': 'recording', 'album': 'release', 'artist': 'artist'}
    endpoint = f'https://musicbrainz.org/ws/2/{types_mb[type]}/{mbid}'

    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'}
    params = {'fmt': 'json'}

    if types_mb[type] == 'artist':
        params['inc'] = 'annotation'

    response = requests.get(endpoint, headers=headers, params=params)
    return response.json() if response.ok else {}


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

    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'}

    response = requests.get(search_endpoint, headers=headers)
    if response.ok:
        data = response.json().get('data', {})
        if data:
            return data[0]

    return {}


def query_lastfm(query: str, type: str, method: str = 'info', mbid=True) -> Dict:
    if not app.config['lastfm_api_key']:
        return {}

    query_lastfm = query.replace(' ', '+')
    endpoint = 'https://ws.audioscrobbler.com/2.0/'

    params = {
        'format': 'json',
        'method': f'{type}.get{method.title()}',
        'api_key': app.config['lastfm_api_key'],
        }

    if mbid:
        params['mbid'] = query
    elif query_lastfm and type != 'user':
        params[type] = query_lastfm


    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'}
    response = requests.get(endpoint, headers=headers, params=params)

    return response.json() if response.ok else {}


def query_wikipedia(query: str) -> Optional[str]:
    if not WIKI_API:
        return None

    query = standard_ascii(query)

    if not query:
        return None

    user_agent = f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'
    wiki = wikipediaapi.Wikipedia(user_agent=user_agent, language='en')
    page = wiki.page(query)

    if page.exists():
        return page.summary

    return None


def trim_text(text, char_limit=300):
    if len(text) <= char_limit:
        return text

    snippet = text[:char_limit]
    period_index = text.find(".", char_limit)

    if period_index != -1:
        snippet = text[:period_index + 1]

    return snippet