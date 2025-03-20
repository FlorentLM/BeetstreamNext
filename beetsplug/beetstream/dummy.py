from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
import flask


# Fake endpoint to avoid some apps errors
@app.route('/rest/scrobble', methods=["GET", "POST"])
@app.route('/rest/scrobble.view', methods=["GET", "POST"])


@app.route('/rest/ping', methods=["GET", "POST"])
@app.route('/rest/ping.view', methods=["GET", "POST"])
def ping():
    r = flask.request.values
    return subsonic_response({}, r.get('f', 'xml'))

@app.route('/rest/getLicense', methods=["GET", "POST"])
@app.route('/rest/getLicense.view', methods=["GET", "POST"])
def get_license():
    r = flask.request.values

    payload = {
        'license': {
            "valid": True
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))

@app.route('/rest/getMusicFolders', methods=["GET", "POST"])
@app.route('/rest/getMusicFolders.view', methods=["GET", "POST"])
def music_folder():
    r = flask.request.values

    payload = {
        'musicFolders': {
            "musicFolder": [{
                "id": 0,
                "name": "Music"     # TODO - This needs to be the real name of beets's config 'directory' key
            }]
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))
