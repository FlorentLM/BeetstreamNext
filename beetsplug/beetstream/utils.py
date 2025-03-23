import unicodedata
from datetime import datetime
import platform
import flask
import json
import base64
import mimetypes
import os
import re
import posixpath
from math import ceil
import xml.etree.cElementTree as ET
from xml.dom import minidom
import shutil
import importlib


API_VERSION = '1.16.1'
BEETSTREAM_VERSION = '1.4.5'

# Prefixes for Beetstream's internal IDs
ART_ID_PREF = 'ar-'
ALB_ID_PREF = 'al-'
SNG_ID_PREF = 'sg-'
PLY_ID_PREF = 'pl-'


DEFAULT_MIME_TYPE = 'application/octet-stream'
EXTENSION_TO_MIME_TYPE_FALLBACK = {
    '.aac'  : 'audio/aac',
    '.flac' : 'audio/flac',
    '.mp3'  : 'audio/mpeg',
    '.mp4'  : 'audio/mp4',
    '.m4a'  : 'audio/mp4',
    '.ogg'  : 'audio/ogg',
    '.opus' : 'audio/opus',
}

FFMPEG_BIN = shutil.which("ffmpeg") is not None
FFMPEG_PYTHON = importlib.util.find_spec("ffmpeg") is not None

if FFMPEG_PYTHON:
    import ffmpeg
elif FFMPEG_BIN:
    import subprocess


# === Beetstream internal IDs makers/readers ===
# These IDs are sent to the client once (when it accesses endpoints such as getArtists or getAlbumList
# and the client will then use these to access a specific item via endpoints that need an ID

def artist_name_to_id(artist_name: str):
    base64_name = base64.urlsafe_b64encode(artist_name.encode('utf-8')).decode('utf-8')
    return f"{ART_ID_PREF}{base64_name}"

def artist_id_to_name(artist_id: str):
    base64_id = artist_id[len(ART_ID_PREF):]
    return base64.urlsafe_b64decode(base64_id.encode('utf-8')).decode('utf-8')

def album_beetid_to_subid(album_id: str):
    return ALB_ID_PREF + album_id

def album_subid_to_beetid(album_id: str):
    return album_id[len(ALB_ID_PREF):]

def song_beetid_to_subid(song_id: str):
    return SNG_ID_PREF + song_id

def song_subid_to_beetid(song_id: str):
    return song_id[len(SNG_ID_PREF):]


# === Mapping functions to translate Beets to Subsonic dict-like structures ===

def map_album(album, songs=None):
    album = dict(album)
    album_id = album_beetid_to_subid(str(album["id"]))
    subsonic_album = {
        'id': album_id,
        'name': album.get('album', ''),
        # 'version': 'Deluxe Edition',                          # TODO
        'artist': album.get('albumartist', ''),
        'year': album.get('year', None),
        'coverArt': album_id,
        # 'starred': '1970-01-01T00:00:00.000Z',                # TODO
        # 'playCount': 1,                                       # TODO
        'genre': album.get('genre', ''),
        'created': timestamp_to_iso(album["added"]),
        'artistId': artist_name_to_id(album["albumartist"]),
        # 'played': '1970-01-01T00:00:00.000Z',                 # TODO
        # 'userRating': '1970-01-01T00:00:00.000Z',             # TODO
        'recordLabels': [{'name': album.get('label', '')}],
        'musicBrainzId': album.get("mb_albumid", ''),
        'genres': [{'name': g for g in stringlist_splitter(album.get('genre', ''))}],
        'displayArtist': album.get('albumartist', ''),
        'sortName': album["album"],
        'originalReleaseDate': {
            'year': album.get('original_year', 0),
            'month': album.get('original_month', 0),
            'day': album.get('original_day', 0)
        },
        'releaseDate': {
            'year': album.get('year', 0),
            'month': album.get('month', 0),
            'day': album.get('day', 0)
        },
        'isCompilation': bool(album.get('comp', False)),
        # 'explicitStatus': 'explicit',                         # TODO

        # These are only needed when part of a directory response
        'isDir': True,
        'parent': artist_name_to_id(album["albumartist"]),

        # These are only needed when part of an albumList or albumList2 response
        'title': album.get('album', ''),
        'album': album.get('album', ''),
    }
    # Add release types if possible
    release_types = stringlist_splitter(album.get('albumtypes', '')) or stringlist_splitter(album.get('albumtype', ''))
    print(album.get('album', ''), type(release_types))
    subsonic_album['releaseTypes'] = [r.strip().title() for r in release_types]

    # Add multi-disc info if needed
    nb_discs = album.get("disctotal", 1)
    if nb_discs > 1:
        subsonic_album["discTitles"] = [
            {
                'disc': d,
                'title': ' - '.join(filter(None, [album.get('album', None), f'Disc {d + 1}']))
            } for d in range(nb_discs)
        ]

    # Used when part of an AlbumID3WithSongs response OR directory ('song' key gets changed to 'child')
    if songs:
        subsonic_album['song'] = list(map(map_song, songs))
        subsonic_album['duration'] = int(sum(s.get('length', 0) for s in subsonic_album['song']))
        subsonic_album['songCount'] = len(subsonic_album['song'])
    else:
        subsonic_album['duration'] = 0
        subsonic_album['songCount'] = 0
        # TODO - These need to be set even when no songs are passed to the mapper...
    return subsonic_album

def map_song(song):
    song = dict(song)
    path = song.get('path', b'').decode('utf-8')
    return {
        'id': song_beetid_to_subid(str(song["id"])),
        'parent': album_beetid_to_subid(str(song["album_id"])),
        'isDir': False,
        'title': song["title"],
        'name': song["title"],
        'album': song["album"],
        'artist': song["albumartist"],
        'track': song["track"],
        'year': song["year"],
        'genre': song["genre"],
        'coverArt': _cover_art_id(song),
        'size': os.path.getsize(path),
        'contentType': path_to_mimetype(path),
        'suffix': song["format"].lower(),
        'duration': ceil(song.get("length", 0)),
        'bitRate': ceil(song.get("bitrate", 0)/1000),
        'path': path,
        'playCount': 1, # TODO
        'created': timestamp_to_iso(song["added"]),
        # "starred": "2019-10-23T04:41:17.107Z",
        'albumId': album_beetid_to_subid(str(song["album_id"])),
        'artistId': artist_name_to_id(song["albumartist"]),
        'type': "music",
        'discNumber': song["disc"]
    }

def map_artist(artist_name):
    artist_id = artist_name_to_id(artist_name)
    return {
        'id': artist_id,
        'name': artist_name,
        # 'sortName': artist_name,
        # TODO
        # "starred": "2021-07-03T06:15:28.757Z", # nothing if not starred
        # 'coverArt': artist_id,
        'albumCount': 1,
        'artistImageUrl': "https://t4.ftcdn.net/jpg/00/64/67/63/360_F_64676383_LdbmhiNM6Ypzb3FM4PPuFP9rHe7ri8Ju.jpg"
    }

def map_playlist(playlist):
    return {
        'id': playlist.id,
        'name': playlist.name,
        'songCount': len(playlist.songs),
        'duration': playlist.duration,
        'created': timestamp_to_iso(playlist.ctime),
        'changed': timestamp_to_iso(playlist.mtime),
        # 'owner': 'userA',     # TODO
        # 'public': True,
    }


# === Core response-formatting functions ===

def dict_to_xml(tag: str, data):
    """ Recursively converts a json-like dict to an XML tree """
    elem = ET.Element(tag)
    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, (dict, list)):
                child = dict_to_xml(key, val)
                elem.append(child)
            else:
                child = ET.Element(key)
                child.text = str(val)
                elem.append(child)
    elif isinstance(data, list):
        for item in data:
            child = dict_to_xml(tag, item)
            elem.append(child)
    else:
        elem.text = str(data)
    return elem

def jsonpify(format: str, data: dict):
    if format == 'jsonp':
        callback = flask.request.values.get("callback")
        return f"{callback}({json.dumps(data)});"
    else:
        return flask.jsonify(data)

def subsonic_response(data: dict = {}, format: str = 'xml', failed=False):
    """ Wrap any json-like dict with the subsonic response elements
     and output the appropriate 'format' (json or xml) """

    if format.startswith('json'):
        wrapped = {
            'subsonic-response': {
                'status': 'failed' if failed else 'ok',
                'version': API_VERSION,
                'type': 'Beetstream',
                'serverVersion': BEETSTREAM_VERSION,
                'openSubsonic': True,
                **data
            }
        }
        return jsonpify(format, wrapped)

    else:
        root = dict_to_xml("subsonic-response", data)
        root.set("xmlns", "http://subsonic.org/restapi")
        root.set("status", 'failed' if failed else 'ok')
        root.set("version", API_VERSION)
        root.set("type", 'Beetstream')
        root.set("serverVersion", BEETSTREAM_VERSION)
        root.set("openSubsonic", 'true')

        xml_str = minidom.parseString(ET.tostring(root, encoding='unicode',
                                                  method='xml', xml_declaration=True)).toprettyxml()

        return flask.Response(xml_str, mimetype="text/xml")


# === Various other utility functions ===

def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def timestamp_to_iso(timestamp):
    return datetime.fromtimestamp(int(timestamp)).isoformat()

def path_to_mimetype(path):
    result = mimetypes.guess_type(path)[0]
    if result:
        return result

    # our mimetype database didn't have information about this file extension.
    base, ext = posixpath.splitext(path)
    result = EXTENSION_TO_MIME_TYPE_FALLBACK.get(ext)
    if result:
        return result

    flask.current_app.logger.warning(f"No mime type mapped for {ext} extension: {path}")

    return DEFAULT_MIME_TYPE

def stringlist_splitter(genres_string):
    delimiters = re.compile('|'.join([';', ',', '/', '\\|']))
    return [g.strip().title()
            .replace('Post ', 'Post-')
            .replace('Prog ', 'Prog-')
            .replace('.', ' ')
            for g in re.split(delimiters, genres_string)]

def creation_date(filepath):
    """ Get a file's creation date
        See: http://stackoverflow.com/a/39501288/1709587 """
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

def _cover_art_id(song):
    if song['album_id']:
        return album_beetid_to_subid(str(song['album_id']))
    return song_beetid_to_subid(str(song['id']))
