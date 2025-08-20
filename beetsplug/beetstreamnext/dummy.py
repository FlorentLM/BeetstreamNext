from beetsplug.beetstreamnext.utils import *
from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext import authentication
import flask


# Fake endpoint to avoid some apps errors
@app.route('/rest/scrobble', methods=["GET", "POST"])
@app.route('/rest/scrobble.view', methods=["GET", "POST"])


@app.route('/rest/ping', methods=["GET", "POST"])
@app.route('/rest/ping.view', methods=["GET", "POST"])
def ping():
    r = flask.request.values
    # authentication.authenticate(r)

    return subsonic_response({}, r.get('f', 'xml'))
