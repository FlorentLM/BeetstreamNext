import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import subsonic_response, subsonic_error


@app.route('/rest/ping', methods=["GET", "POST"])
@app.route('/rest/ping.view', methods=["GET", "POST"])
def endpoint_ping():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    return subsonic_response({}, resp_fmt=resp_fmt)


@app.route('/rest/startScan', methods=["GET", "POST"])
@app.route('/rest/startScan.view', methods=["GET", "POST"])
def endpoint_start_scan():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)

    # TODO: maybe trigger a refresh of BeetstreamNext's data (album covers, etc)?

    return subsonic_response({}, resp_fmt=resp_fmt)


@app.route('/rest/getScanStatus', methods=["GET", "POST"])
@app.route('/rest/getScanStatus.view', methods=["GET", "POST"])
def endpoint_get_scan_status():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)

    payload = {
        'scanStatus': {
            "scanning": False,
            "count": len(flask.g.lib.items())
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


@app.route('/rest/tokenInfo', methods=["GET", "POST"])
@app.route('/rest/tokenInfo.view', methods=["GET", "POST"])
def endpoint_token_info():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    api_key = r.get('apiKey', default='', type=str)

    if not api_key:
        return subsonic_error(10, resp_fmt=resp_fmt)

    from beetsplug.beetstreamnext.users import load_username
    import hashlib

    api_key_hash = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
    username = load_username(api_key_hash)

    if not username:
        return subsonic_error(40, resp_fmt=resp_fmt)

    payload = {
        'tokenInfo': {
            'username': username
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


@app.route('/rest/getPodcasts', methods=["GET", "POST"])
@app.route('/rest/getPodcasts.view', methods=["GET", "POST"])
def endpoint_get_podcasts():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    return subsonic_error(0, message='Podcast feature is not supported.', resp_fmt=resp_fmt)


@app.route('/rest/getInternetRadioStations', methods=["GET", "POST"])
@app.route('/rest/getInternetRadioStations.view', methods=["GET", "POST"])
def endpoint_get_radios():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    return subsonic_error(0, message='Radio feature is not supported.', resp_fmt=resp_fmt)