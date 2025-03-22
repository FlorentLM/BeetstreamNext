from beetsplug.beetstream import ALB_ID_PREF, ART_ID_PREF, SNG_ID_PREF
import unicodedata
from datetime import datetime
from typing import Union
import beets
import subprocess
import platform
import flask
import json
import base64
import mimetypes
import os
import re
import posixpath
import xml.etree.cElementTree as ET
from math import ceil
from xml.dom import minidom

API_VERSION = '1.16.1'

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

def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def timestamp_to_iso(timestamp):
    return datetime.fromtimestamp(int(timestamp)).isoformat()

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
                'serverVersion': '1.4.5',
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
        root.set("serverVersion", '1.4.5')
        root.set("openSubsonic", 'true')

        xml_str = minidom.parseString(ET.tostring(root, encoding='unicode',
                                                  method='xml', xml_declaration=True)).toprettyxml()

        return flask.Response(xml_str, mimetype="text/xml")


def map_album(album):
    album = dict(album)
    return {
        "id": album_beetid_to_subid(str(album["id"])),
        "name": album["album"],
        "title": album["album"],
        "album": album["album"],
        "artist": album["albumartist"],
        "artistId": artist_name_to_id(album["albumartist"]),
        "parent": artist_name_to_id(album["albumartist"]),
        "isDir": True,
        "coverArt": album_beetid_to_subid(str(album["id"])) or "",
        "songCount": 1, # TODO
        "duration": 1, # TODO
        "playCount": 1, # TODO
        "created": timestamp_to_iso(album["added"]),
        "year": album["year"],
        "genre": album["genre"],
        "starred": "1970-01-01T00:00:00.000Z", # TODO
        "averageRating": 0 # TODO
    }

def map_album_list(album):
    album = dict(album)
    return {
        "id": album_beetid_to_subid(str(album["id"])),
        "parent": artist_name_to_id(album["albumartist"]),
        "isDir": True,
        "title": album["album"],
        "album": album["album"],
        "artist": album["albumartist"],
        "year": album["year"],
        "genre": album["genre"],
        "coverArt": album_beetid_to_subid(str(album["id"])) or "",
        "userRating": 5, # TODO
        "averageRating": 5, # TODO
        "playCount": 1,  # TODO
        "created": timestamp_to_iso(album["added"]),
        "starred": ""
    }

def map_song(song):
    song = dict(song)
    path = song["path"].decode('utf-8')
    return {
        "id": song_beetid_to_subid(str(song["id"])),
        "parent": album_beetid_to_subid(str(song["album_id"])),
        "isDir": False,
        "title": song["title"],
        "name": song["title"],
        "album": song["album"],
        "artist": song["albumartist"],
        "track": song["track"],
        "year": song["year"],
        "genre": song["genre"],
        "coverArt": _cover_art_id(song),
        "size": os.path.getsize(path),
        "contentType": path_to_mimetype(path),
        "suffix": song["format"].lower(),
        "duration": ceil(song["length"]),
        "bitRate": ceil(song["bitrate"]/1000),
        "path": path,
        "playCount": 1, #TODO
        "created": timestamp_to_iso(song["added"]),
        # "starred": "2019-10-23T04:41:17.107Z",
        "albumId": album_beetid_to_subid(str(song["album_id"])),
        "artistId": artist_name_to_id(song["albumartist"]),
        "type": "music",
        "discNumber": song["disc"]
    }

def _cover_art_id(song):
    if song['album_id']:
        return album_beetid_to_subid(str(song['album_id']))
    return song_beetid_to_subid(str(song['id']))

def map_artist(artist_name):
    return {
        "id": artist_name_to_id(artist_name),
        "name": artist_name,
        # TODO
        # "starred": "2021-07-03T06:15:28.757Z", # nothing if not starred
        "coverArt": "",
        "albumCount": 1,
        "artistImageUrl": "https://t4.ftcdn.net/jpg/00/64/67/63/360_F_64676383_LdbmhiNM6Ypzb3FM4PPuFP9rHe7ri8Ju.jpg"
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


def genres_splitter(genres_string):
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