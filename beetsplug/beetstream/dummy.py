from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
from beetsplug.beetstream import authentication
import flask


# Fake endpoint to avoid some apps errors
@app.route('/rest/scrobble', methods=["GET", "POST"])
@app.route('/rest/scrobble.view', methods=["GET", "POST"])


@app.route('/rest/ping', methods=["GET", "POST"])
@app.route('/rest/ping.view', methods=["GET", "POST"])
def ping():
    r = flask.request.values
    authentication.authenticate(r)

    return subsonic_response({}, r.get('f', 'xml'))
