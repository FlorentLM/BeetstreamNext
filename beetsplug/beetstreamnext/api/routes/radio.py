import flask

from .. import api_bp

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.radio import create_station, update_station, delete_station
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.api.serializers import map_radio_station


def radios_payload() -> dict:

    with database() as db:
        rows = db.execute(
            """
            SELECT id, name, stream_url, homepage_url 
            FROM internet_radio_stations
            """
        ).fetchall()

    payload = {
        'internetRadioStations': {
            'internetRadioStation': [map_radio_station(dict(row)) for row in rows]
        }
    }

    return payload


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getInternetRadioStations/
@api_bp.route('/getInternetRadioStations', methods=['GET', 'POST'])
@api_bp.route('/getInternetRadioStations.view', methods=['GET', 'POST'])
def endpoint_get_radio_stations() -> flask.Response:

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    payload = radios_payload()
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/createInternetRadioStation/
@api_bp.route('/createInternetRadioStation', methods=['GET', 'POST'])
@api_bp.route('/createInternetRadioStation.view', methods=['GET', 'POST'])
def endpoint_create_radio_station() -> flask.Response:

    if not flask.g.user_data.get('adminRole'):
        return subsonic_error(40, message='Only admins can manage radio stations.')

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    name = r.get('name', type=safe_str)
    stream_url = r.get('streamUrl', type=str)           # Required
    homepage_url = r.get('homepageUrl', type=str)       # Required

    if not name or not stream_url:
        return subsonic_error(10, resp_fmt=resp_fmt)

    create_station(name, stream_url, homepage_url)
    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/updateInternetRadioStation/
@api_bp.route('/updateInternetRadioStation', methods=['GET', 'POST'])
@api_bp.route('/updateInternetRadioStation.view', methods=['GET', 'POST'])
def endpoint_update_radio_station() -> flask.Response:

    if not flask.g.user_data.get('adminRole'):
        return subsonic_error(40)

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    sid = r.get('id', type=int)                     # Required
    name = r.get('name', type=safe_str)             # Required
    stream_url = r.get('streamUrl', type=str)       # Required
    homepage_url = r.get('homepageUrl', type=str)

    if not all([sid, name, stream_url]):
        return subsonic_error(10, resp_fmt=resp_fmt)

    update_station(sid, name, stream_url, homepage_url)
    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/deleteInternetRadioStation/
@api_bp.route('/deleteInternetRadioStation', methods=['GET', 'POST'])
@api_bp.route('/deleteInternetRadioStation.view', methods=['GET', 'POST'])
def endpoint_delete_radio_station() -> flask.Response:

    if not flask.g.user_data.get('adminRole'):
        return subsonic_error(40)

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    sid = r.get('id', type=int)                             # Required

    if not sid:
        return subsonic_error(10, resp_fmt=resp_fmt)

    delete_station(sid)
    return subsonic_response({}, resp_fmt=resp_fmt)