import json
import re
from typing import Optional, Dict
from xml.etree import ElementTree as ET

import flask

from beetsplug.beetstreamnext.constants import SUBSONIC_API_VER, BEETSTREAMNEXT_VER, ALPHANUM_CHARS
from beetsplug.beetstreamnext.utils import safe_str


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
                'version': SUBSONIC_API_VER,
                'type': 'BeetstreamNext',
                'serverVersion': BEETSTREAMNEXT_VER,
                'openSubsonic': True,
                **data
            }
        }
        return jsonpify(resp_fmt, wrapped)

    else:
        root = dict_to_xml("subsonic-response", data)
        root.set("xmlns", "http://subsonic.org/restapi")
        root.set("status", 'failed' if failed else 'ok')
        root.set("version", SUBSONIC_API_VER)
        root.set("type", 'BeetstreamNext')
        root.set("serverVersion", BEETSTREAMNEXT_VER)
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
        callback = flask.request.values.get('callback', default='callback', type=safe_str)
        if not re.match(ALPHANUM_CHARS, callback):
            return flask.Response("Invalid callback parameter", status=400)
        return flask.Response(f"{callback}({json.dumps(data)});", mimetype='application/javascript')
    else:
        return flask.jsonify(data)
