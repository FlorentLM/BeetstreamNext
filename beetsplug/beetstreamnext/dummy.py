import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import subsonic_response, subsonic_error


@app.route('/rest/ping', methods=["GET", "POST"])
@app.route('/rest/ping.view', methods=["GET", "POST"])
def ping():
    r = flask.request.values
    return subsonic_response({}, r.get('f', 'xml'))


@app.route('/rest/tokenInfo', methods=["GET", "POST"])
@app.route('/rest/tokenInfo.view', methods=["GET", "POST"])
def token_info():
    r = flask.request.values
    resp_fmt = r.get('f', 'xml')

    api_key = r.get('apiKey', '')
    if not api_key:
        return subsonic_error(10, resp_fmt=resp_fmt)

    from beetsplug.beetstreamnext.authentication import get_user
    import hashlib

    api_key_hash = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
    username = get_user(api_key_hash)

    if not username:
        return subsonic_error(40, resp_fmt=resp_fmt)

    return subsonic_response({'tokenInfo': {'username': username}}, resp_fmt)