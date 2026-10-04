from __future__ import annotations
import flask

from .. import subsonic_bp

from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.services.radio import create_station, update_station, delete_station
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.blueprints.subsonic.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.library.resolve import Resolve
from beetsplug.beetstreamnext.core.library.serialise import Serialise

def radios_payload(username: str) -> dict:

    with database() as db:
        rows = db.execute(
            """
            SELECT id, name, stream_url, homepage_url 
            FROM internet_radio_stations
            WHERE owner = ?
            """, (username,)
        ).fetchall()

    payload = {
        'internetRadioStations': {
            'internetRadioStation': [Serialise.radio(dict(row)) for row in rows]
        }
    }

    return payload


def _own_station(raw_id: str):
    """The station if it belongs to the caller, else None."""

    station = Resolve.radio(raw_id)
    if station is None:
        return None

    if station['owner'] != flask.g.username:
        return None

    return station


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getInternetRadioStations/
@subsonic_bp.route('/getInternetRadioStations', methods=['GET', 'POST'])
@subsonic_bp.route('/getInternetRadioStations.view', methods=['GET', 'POST'])
def endpoint_get_radio_stations() -> flask.Response:

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    payload = radios_payload(flask.g.username)
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/createInternetRadioStation/
@subsonic_bp.route('/createInternetRadioStation', methods=['GET', 'POST'])
@subsonic_bp.route('/createInternetRadioStation.view', methods=['GET', 'POST'])
def endpoint_create_radio_station() -> flask.Response:

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    stream_url = r.get('streamUrl', type=str)           # Required
    name = r.get('name', type=safe_str)                 # Required
    homepage_url = r.get('homepageUrl', type=str)

    if not name or not stream_url:
        return subsonic_error(10, resp_fmt=resp_fmt)

    station_id, error = create_station(flask.g.username, name, stream_url, homepage_url)
    if station_id is None:
        return subsonic_error(0, message=error, resp_fmt=resp_fmt)

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/updateInternetRadioStation/
@subsonic_bp.route('/updateInternetRadioStation', methods=['GET', 'POST'])
@subsonic_bp.route('/updateInternetRadioStation.view', methods=['GET', 'POST'])
def endpoint_update_radio_station() -> flask.Response:

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    raw_id = r.get('id', default='', type=safe_str)     # Required
    stream_url = r.get('streamUrl', type=str)           # Required
    name = r.get('name', type=safe_str)                 # Required
    homepage_url = r.get('homepageUrl', type=str)

    if not raw_id or not name or not stream_url:
        return subsonic_error(10, resp_fmt=resp_fmt)

    station = _own_station(raw_id)
    if station is None:
        return subsonic_error(70, resp_fmt=resp_fmt)

    error = update_station(station['id'], name, stream_url, homepage_url, image=station['image'], owner=station['owner'])
    if error:
        return subsonic_error(0, message=error, resp_fmt=resp_fmt)

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/deleteInternetRadioStation/
@subsonic_bp.route('/deleteInternetRadioStation', methods=['GET', 'POST'])
@subsonic_bp.route('/deleteInternetRadioStation.view', methods=['GET', 'POST'])
def endpoint_delete_radio_station() -> flask.Response:

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    raw_id = r.get('id', default='', type=safe_str)          # Required

    if not raw_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    station = _own_station(raw_id)
    if station is None:
        return subsonic_error(70, resp_fmt=resp_fmt)

    delete_station(station['id'], station['owner'])
    return subsonic_response({}, resp_fmt=resp_fmt)