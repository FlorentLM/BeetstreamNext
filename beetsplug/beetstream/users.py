from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
import flask


@app.route('/rest/getUser', methods=["GET", "POST"])
@app.route('/rest/getUser.view', methods=["GET", "POST"])
def get_user():
    r = flask.request.values

    # TODO - Proper user management

    payload = {
        'user': {
            "username" : "admin",
            "email" : "foo@example.com",
            "scrobblingEnabled" : True,
            "adminRole" : True,
            "settingsRole" : True,
            "downloadRole" : True,
            "uploadRole" : True,
            "playlistRole" : True,
            "coverArtRole" : True,
            "commentRole" : True,
            "podcastRole" : True,
            "streamRole" : True,
            "jukeboxRole" : True,
            "shareRole" : True,
            "videoConversionRole" : True,
            "avatarLastChanged" : "1970-01-01T00:00:00.000Z",
            "folder" : [ 0 ]
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))

