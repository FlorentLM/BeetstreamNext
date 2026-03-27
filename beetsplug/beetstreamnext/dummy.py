import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import subsonic_response, subsonic_error


@app.route('/rest/ping', methods=["GET", "POST"])
@app.route('/rest/ping.view', methods=["GET", "POST"])
def endpoint_ping():
    r = flask.request.values
    return subsonic_response({}, r.get('f', 'xml'))


@app.route('/rest/startScan', methods=["GET", "POST"])
@app.route('/rest/startScan.view', methods=["GET", "POST"])
def endpoint_start_scan():
    r = flask.request.values

    # TODO: maybe trigger a refresh of BeetstreamNext's data (album covers, etc)?

    return subsonic_response({}, r.get('f', 'xml'))


@app.route('/rest/getScanStatus', methods=["GET", "POST"])
@app.route('/rest/getScanStatus.view', methods=["GET", "POST"])
def endpoint_get_scan_status():
    r = flask.request.values

    payload = {
        'scanStatus': {
            "scanning": False,
            "count": len(flask.g.lib.items())
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/tokenInfo', methods=["GET", "POST"])
@app.route('/rest/tokenInfo.view', methods=["GET", "POST"])
def endpoint_token_info():
    r = flask.request.values
    resp_fmt = r.get('f', 'xml')

    api_key = r.get('apiKey', '')
    if not api_key:
        return subsonic_error(10, resp_fmt=resp_fmt)

    from beetsplug.beetstreamnext.users import load_username
    import hashlib

    api_key_hash = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
    username = load_username(api_key_hash)

    if not username:
        return subsonic_error(40, resp_fmt=resp_fmt)

    return subsonic_response({'tokenInfo': {'username': username}}, resp_fmt)


@app.route('/rest/getPodcasts', methods=["GET", "POST"])
@app.route('/rest/getPodcasts.view', methods=["GET", "POST"])
def endpoint_get_podcasts():
    return subsonic_error(0, message='Feature not supported.', resp_fmt=flask.request.values.get('f', 'xml'))


@app.route('/rest/getInternetRadioStations', methods=["GET", "POST"])
@app.route('/rest/getInternetRadioStations.view', methods=["GET", "POST"])
def endpoint_get_radios():
    return subsonic_error(0, message='Feature not supported.', resp_fmt=flask.request.values.get('f', 'xml'))