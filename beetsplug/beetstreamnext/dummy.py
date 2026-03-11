import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import subsonic_response


# Fake endpoint to avoid some apps errors
@app.route('/rest/scrobble', methods=["GET", "POST"])
@app.route('/rest/scrobble.view', methods=["GET", "POST"])
def scrobble():
    r = flask.request.values
    return subsonic_response({}, r.get('f', 'xml'))


@app.route('/rest/ping', methods=["GET", "POST"])
@app.route('/rest/ping.view', methods=["GET", "POST"])
def ping():
    r = flask.request.values
    return subsonic_response({}, r.get('f', 'xml'))
