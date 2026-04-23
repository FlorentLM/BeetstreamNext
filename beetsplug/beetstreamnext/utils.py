import binascii
import string
from typing import Optional, Dict, List, Tuple, Any, Sequence
import threading
import os
import shutil
import platform
import importlib
from functools import lru_cache
from datetime import datetime, timezone
import re
import json
import base64
import mimetypes
import unicodedata
from urllib.parse import unquote
import xml.etree.ElementTree as ET
# from xml.dom import minidom
import flask
from sqlite3 import Connection
from beets.dbcore.db import Transaction

from beetsplug.beetstreamnext.constants import (
    SUBSONIC_API_VERSION, ART_MBID_PREF, ART_NAME_PREF, ALB_ID_PREF, SNG_ID_PREF,
    BEETS_MULTI_DELIM, GENRES_DELIM, ASCII_TRANSLATE_TABLE, BEETSTREAMNEXT_VERSION
)


FFMPEG_BIN = shutil.which("ffmpeg") is not None
FFMPEG_PYTHON = importlib.util.find_spec("ffmpeg") is not None

if FFMPEG_PYTHON:
    import ffmpeg
elif FFMPEG_BIN:
    ffmpeg = None


##
# General flask helpers

def grab_auth_params() -> Dict[str, str]:
    r = flask.request.values

    auth_params = {k: r.get(k, default='', type=str) for k in ['s', 't', 'p', 'apiKey'] if k in r}
    other_auth_params = {k: r.get(k, default='', type=safe_str) for k in ['u', 'c', 'v'] if k in r}
    auth_params.update(other_auth_params)

    return auth_params


def imageart_url(item_id: str, size: Optional[int] = None) -> str:
    if not item_id:
        return ''

    # check if the base URL is already built for the current request, if not, build it
    base_url = getattr(flask.g, '_art_base_url', None)
    if not base_url:
        base_url = flask.url_for('endpoint_get_cover_art', _external=True, **grab_auth_params())
        flask.g._art_base_url = base_url

    sep = '&' if '?' in base_url else '?'
    url = f"{base_url}{sep}id={item_id}"
    if size:
        url += f"&size={size}"
    return url


##
# Main response and error payloads

def subsonic_response(data: Optional[Dict] = None, resp_fmt: str = 'xml', failed: bool = False) -> flask.Response:
    """
    Wraps json-like dict with the subsonic response data and
    outputs the appropriate format (json or xml).
    """
    data = data or {}

    if resp_fmt.startswith('json'):
        wrapped = {
            'subsonic-response': {
                'status': 'failed' if failed else 'ok',
                'version': SUBSONIC_API_VERSION,
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
        root.set("version", SUBSONIC_API_VERSION)
        root.set("type", 'BeetstreamNext')
        root.set("serverVersion", BEETSTREAMNEXT_VERSION)
        root.set("openSubsonic", 'true')

        xml_bytes = ET.tostring(root, encoding='UTF-8', method='xml', xml_declaration=True)
        # xml_bytes = minidom.parseString(xml_bytes).toprettyxml(encoding='UTF-8')
        xml_str = xml_bytes.decode('UTF-8')

        return flask.Response(xml_str, mimetype="text/xml")


def subsonic_error(code: int = 0, message: str = '', resp_fmt: str = 'xml') -> flask.Response:

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


##
# BeetstreamNext internal IDs mappers

def beets_to_sub_artist(name_or_mbid: str, is_mbid: bool = True) -> str:
    encoded = base64.urlsafe_b64encode(str(name_or_mbid).encode('utf-8')).rstrip(b'=').decode('utf-8')
    prefix = ART_MBID_PREF if is_mbid else ART_NAME_PREF
    return f"{prefix}{encoded}"


def sub_to_beets_artist(subsonic_artist_id: str) -> Tuple[str, bool]:
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

def beets_to_sub_album(beet_album_id) -> str:
    return f'{ALB_ID_PREF}{beet_album_id}'

def sub_to_beets_album(subsonic_album_id) -> int | None:
    try:
        return int(str(subsonic_album_id)[len(ALB_ID_PREF):])
    except (ValueError, IndexError):
        return None

def beets_to_sub_song(beet_song_id) -> str:
    return f'{SNG_ID_PREF}{beet_song_id}'

def sub_to_beets_song(subsonic_song_id) -> int | None:
    try:
        return int(str(subsonic_song_id)[len(SNG_ID_PREF):])
    except (ValueError, IndexError):
        return None


##


##
# Mapping functions to translate Beets to OpenSubsonic dict-like structures
# TODO - Support multiartists lists!!! See https://opensubsonic.netlify.app/docs/responses/child/


## Requests format conversions

def _clean_xml_key(key: str) -> str:
    safe = re.sub(r'[^a-zA-Z0-9_\-.]', '_', str(key))
    # XML tags cant start with a number, hyphen or dot
    if re.match(r'^[^a-zA-Z_]', safe):
        safe = '_' + safe
    return safe


def dict_to_xml(tag: str, data) -> ET.Element[str]:
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
        key = _clean_xml_key(key)
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
                if not val:
                    ET.SubElement(elem, _clean_xml_key(key))
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


def jsonpify(format: str, data: dict) -> flask.Response:
    if format == 'jsonp':
        callback = flask.request.values.get("callback")
        return flask.Response(f"{callback}({json.dumps(data)});", mimetype='application/javascript')
    else:
        return flask.jsonify(data)


##
# Text utilities

def remove_accents(text: Any) -> str:
    if not text:
        return ''
    return ''.join(c for c in unicodedata.normalize('NFD', str(text)) if unicodedata.category(c) != 'Mn')


def split_beets_multi(stringlist: Sequence[Any] | str) -> List[str]:
    """Split a beets multi-value field."""
    if not stringlist:
        return []

    if not isinstance(stringlist, str) and isinstance(stringlist, Sequence):
        # re-join if it's a sequence
        stringlist = BEETS_MULTI_DELIM.join(stringlist)

    splitted = str(stringlist).split(BEETS_MULTI_DELIM)
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
    return text.translate(ASCII_TRANSLATE_TABLE).strip()


def trim_text(text: str, char_limit: int = 300) -> str:
    if len(text) <= char_limit:
        return text

    snippet = text[:char_limit]
    period_index = text.find(".", char_limit)

    if period_index != -1:
        snippet = text[:period_index + 1]

    return snippet


##
# Various parsers / converters / formatters

def escape_like(s: str, escape: str = '!') -> str:
    """Escape SQL LIKE wildcards. Use with `LIKE ? ESCAPE '!'`."""
    return s.replace(escape, escape * 2).replace('%', escape + '%').replace('_', escape + '_')


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
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat().replace('+00:00', 'Z')
    except (ValueError, TypeError):
        return ''


@lru_cache(maxsize=1024)
def genres_formatter(genres: Optional[str]) -> Tuple[str, ...]:
    """Additional cleaning for common genres formatting issues."""
    if not genres:
        return ()

    raw_list = split_beets_multi(genres)
    separated = GENRES_DELIM.split(';'.join(raw_list))

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

def creation_date(filepath) -> float:
    """Get a file's creation date."""

    if platform.system() == 'Windows':
        return os.path.getctime(filepath)

    stat = os.stat(filepath)

    if platform.system() == 'Darwin':
        return stat.st_birthtime

    # Linux: fall back to mtime
    return getattr(stat, 'st_birthtime', stat.st_mtime)


def get_mimetype(path) -> str:

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


def chunked_query(
        db_obj: 'Transaction | Connection',
        query_template: str,
        chunked_values: List[Any],
        base_params: Optional[List[Any]] = None,
        chunk_size=900
    ) -> List[Any]:
    """
    db_obj: The beets Transaction or sqlite Connection object
    query_template: SQL string with a '{q}' placeholder for the IN clause
    chunked_values: The list of values to query
    base_params: Static parameters to bind before the chunked values
    """
    base_params = base_params or []
    results = []

    for i in range(0, len(chunked_values), chunk_size):
        chunk = chunked_values[i: i + chunk_size]
        question_marks = ','.join(['?'] * len(chunk))
        sql = query_template.replace('{q}', question_marks)
        params = base_params + chunk

        if isinstance(db_obj, Transaction):
            chunk_results = list(db_obj.query(sql, params))
        else:
            chunk_results = db_obj.execute(sql, params).fetchall()
        results.extend(chunk_results)
    return results