import sqlite3
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.db import load_userdata
from beetsplug.beetstreamnext.utils import subsonic_error, subsonic_response


def _user_payload(user_data: dict) -> dict:
    """Build a Subsonic user dict from a load_userdata result."""
    return {
        'username':            user_data.get('username', ''),
        'email':               user_data.get('email', '') or '',
        'scrobblingEnabled':   bool(user_data.get('scrobblingEnabled', False)),
        'maxBitRate':          user_data.get('maxBitRate', 0),
        'adminRole':           bool(user_data.get('adminRole', False)),
        'settingsRole':        bool(user_data.get('settingsRole', True)),
        'downloadRole':        bool(user_data.get('downloadRole', False)),
        'uploadRole':          bool(user_data.get('uploadRole', False)),
        'playlistRole':        bool(user_data.get('playlistRole', True)),
        'coverArtRole':        bool(user_data.get('coverArtRole', False)),
        'commentRole':         bool(user_data.get('commentRole', True)),
        'podcastRole':         bool(user_data.get('podcastRole', False)),
        'streamRole':          bool(user_data.get('streamRole', True)),
        'jukeboxRole':         bool(user_data.get('jukeboxRole', False)),
        'shareRole':           bool(user_data.get('shareRole', False)),
        'videoConversionRole': bool(user_data.get('videoConversionRole', False)),
        'folder':              [user_data.get('folder', 0)],
    }


@app.route('/rest/getUser', methods=["GET", "POST"])
@app.route('/rest/getUser.view', methods=["GET", "POST"])
def get_user():
    r = flask.request.values
    resp_fmt = r.get('f', 'xml')

    requesting_user_data = flask.g.user_data
    if not requesting_user_data:
        return subsonic_error(40, resp_fmt=resp_fmt)

    # 'username' param lets an admin query any user (non-admins can only query themselves)
    requested_username = r.get('username', flask.g.username)
    if requested_username != flask.g.username and not requesting_user_data.get('adminRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if requested_username != flask.g.username:
        target_data = load_userdata(requested_username)
        if not target_data:
            return subsonic_error(70, resp_fmt=resp_fmt)
    else:
        target_data = requesting_user_data

    payload = {'user': _user_payload(target_data)}
    return subsonic_response(payload, resp_fmt)


@app.route('/rest/getUsers', methods=["GET", "POST"])
@app.route('/rest/getUsers.view', methods=["GET", "POST"])
def get_users():
    r = flask.request.values
    resp_fmt = r.get('f', 'xml')

    requesting_user_data = flask.g.user_data
    if not requesting_user_data or not requesting_user_data.get('adminRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    db_path = flask.current_app.config['DB_PATH']
    with sqlite3.connect(db_path) as conn:
        usernames = [row[0] for row in conn.execute("SELECT username FROM users").fetchall()]

    users = []
    for uname in usernames:
        data = load_userdata(uname)
        if data:
            users.append(_user_payload(data))

    payload = {'users': {'user': users}}
    return subsonic_response(payload, resp_fmt)