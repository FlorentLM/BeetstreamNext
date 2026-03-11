import unicodedata
from datetime import datetime
import platform
from pathlib import Path
from typing import Union
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

    artist_name = beets_object.get('albumartist', '')

    # Common fields to albums and songs
    subsonic_media = {
        'artist': artist_name,
        'artistId': beets_to_sub_artist(artist_name),
        'displayArtist': artist_name,
        'displayAlbumArtist': artist_name,
        'album': beets_object.get('album', ''),
        'year': beets_object.get('year', 0),
        'genre': beets_object.get('genre', ''),
        'genres': [{'name': g} for g in genres_formatter(beets_object.get('genre', ''))],
        'created': timestamp_to_iso(beets_object.get('added')) or datetime.now().isoformat(),   # default to now?
        'originalReleaseDate': {
            'year': beets_object.get('original_year', 0) or beets_object.get('year', 0),
            'month': beets_object.get('original_month', 0) or beets_object.get('month', 0),
            'day': beets_object.get('original_day', 0) or beets_object.get('day', 0)
        },
        'releaseDate': {
            'year': beets_object.get('year', 0),
            'month': beets_object.get('month', 0),
            'day': beets_object.get('day', 0)
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

        # 'starred': timestamp_to_iso(album.get('last_liked_album', 0)),
        'userRating': album.get('stars_rating_album', 0),

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
    songs_ratings = [s.get('stars_rating', 0) for s in subsonic_album.get('song', []) if s.get('stars_rating', 0)]
    subsonic_album['averageRating'] = sum(songs_ratings) / len(songs_ratings) if songs_ratings else 0

    return subsonic_album

def map_song(song_object):
    song = dict(song_object)

    subsonic_song = map_media(song)

    song_id = beets_to_sub_song(song.get('id', 0))
    song_name = song.get('title', '')
    song_filepath = song.get('path', b'').decode('utf-8')

    album_id = beets_to_sub_album(song.get('album_id', 0))

    song_specific = {
        'id': song_id,
        'musicBrainzId': song.get('mb_albumid', ''),
        'name': song_name,
        'sortName': song_name,
        'albumId': album_id,
        'coverArt': album_id or song_id,

        'track': song.get('track', 1),
        'path': song_filepath if os.path.isfile(song_filepath) else '',

        'played': timestamp_to_iso(song.get('last_played', 0)),
        # 'starred': timestamp_to_iso(song.get('last_liked', 0)),
        'playCount': song.get('play_count', 0),
        'userRating': song.get('stars_rating', 0),

        'duration': round(song.get('length', 0)),
        'bpm': song.get('bpm', 0),
        'bitRate': round(song.get('bitrate', 0) / 1000) or 0,
        'bitDepth': song.get('bitdepth', 0),
        'samplingRate': song.get('samplerate', 0),
        'channelCount': song.get('channels', 2),
        'discNumber': song.get('disc', 0),
        'comment': song.get('comment', ''),

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

    subsonic_song['suffix'] = song.get('format').lower() or subsonic_song['path'].rsplit('.', 1)[-1].lower()
    subsonic_song['size'] = os.path.getsize(subsonic_song['path']) or round(song.get('bitrate', 0) * song.get('length', 0) / 8)
    subsonic_song['contentType'] = get_mimetype(subsonic_song.get('path', None) or subsonic_song.get('suffix', None))

    return subsonic_song


def map_artist(artist_name, with_albums=True):
    subsonic_artist_id = beets_to_sub_artist(artist_name)

    subsonic_artist = {
        'id': subsonic_artist_id,
        'name': artist_name,
        'sortName': artist_name,
        'title': artist_name,
        # "starred": "2021-07-03T06:15:28.757Z", # nothing if not starred
        'coverArt': subsonic_artist_id,
        "userRating": 0,

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

    albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))
    subsonic_artist['albumCount'] = len(albums)
    if albums:
        subsonic_artist['musicBrainzId'] = albums[0].get('mb_albumartistid', '')

        if with_albums:
            subsonic_artist['album'] = list(map(partial(map_album, with_songs=False), albums))

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


# === Core response-formatting functions ===

def dict_to_xml(tag: str, data):
    """
    Converts a json-like dict to an XML tree where every key/value pair
    with a simple value is mapped as an attribute.... unless if adding the attribute
    would create a duplicate, in which case a new element with that tag is created instead
    """
    elem = ET.Element(tag)

    if isinstance(data, dict):
        for key, val in data.items():
            if not isinstance(val, (dict, list)):
                # If the attribute already exists, create a child element
                if key in elem.attrib:
                    child = ET.Element(key)
                    child.text = str(val).lower() if isinstance(val, bool) else str(val)
                    elem.append(child)
                else:
                    elem.set(key, str(val).lower() if isinstance(val, bool) else str(val))
            elif isinstance(val, list):
                for item in val:
                    # For each item in the list, process depending on type
                    if not isinstance(item, (dict, list)):
                        if key in elem.attrib:
                            child = ET.Element(key)
                            child.text = str(item).lower() if isinstance(item, bool) else str(item)
                            elem.append(child)
                        else:
                            elem.set(key, str(item).lower() if isinstance(item, bool) else str(item))
                    else:
                        child = dict_to_xml(key, item)
                        elem.append(child)
            elif isinstance(val, dict):
                child = dict_to_xml(key, val)
                elem.append(child)

    elif isinstance(data, list):
        # when data is a list, each item becomes a new child
        for item in data:
            if not isinstance(item, (dict, list)):
                if tag in elem.attrib:
                    child = ET.Element(tag)
                    child.text = str(item).lower() if isinstance(item, bool) else str(item)
                    elem.append(child)
                else:
                    elem.set(tag, str(item).lower() if isinstance(item, bool) else str(item))
            else:
                child = dict_to_xml(tag, item)
                elem.append(child)
    else:
        elem.text = str(data).lower() if isinstance(data, bool) else str(data)

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

def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def timestamp_to_iso(timestamp):
    return datetime.fromtimestamp(timestamp if timestamp else 0).isoformat()

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

def stringlist_splitter(delimiter_separated_string: str):
    delimiters = re.compile('|'.join([';', ',', '/', '\\|']))
    return re.split(delimiters, delimiter_separated_string)

def genres_formatter(genres):
    """Additional cleaning for common genres formatting issues."""
    if isinstance(genres, str):
        genres = stringlist_splitter(genres)
    return [g.strip().title()
            .replace('Post ', 'Post-')
            .replace('Prog ', 'Progressive ')
            .replace('Rnb', 'R&B')
            .replace("R'N'B", 'R&B')
            .replace("R 'N' B", 'R&B')
            .replace('Rock & ', 'Rock and ')
            .replace("Rock'N'", 'Rock and')
            .replace("Rock 'N'", 'Rock and')
            .replace('.', ' ')
            for g in genres]

def creation_date(filepath):
    """
    Get a file's creation date.
    (see: http://stackoverflow.com/a/39501288/1709587)
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


def query_deezer(query: str, type: str):

    query_urlsafe = urllib.parse.quote_plus(query.replace(' ', '-'))
    endpoint = f'https://api.deezer.com/{type}/{query_urlsafe}'

    headers = {'User-Agent': f'BeetstreamNext/{BEETSTREAMNEXT_VERSION} ( https://github.com/FlorentLM/BeetstreamNext )'}

    response = requests.get(endpoint, headers=headers)

    return response.json() if response.ok else {}


def query_lastfm(query: str, type: str, method: str = 'info', mbid=True):
    if not app.config['lastfm_api_key']:
        return {}

    query_lastfm = query.replace(' ', '+')
    endpoint = 'http://ws.audioscrobbler.com/2.0/'

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


def trim_text(text, char_limit=300):
    if len(text) <= char_limit:
        return text

    snippet = text[:char_limit]
    period_index = text.find(".", char_limit)

    if period_index != -1:
        snippet = text[:period_index + 1]

    return snippet