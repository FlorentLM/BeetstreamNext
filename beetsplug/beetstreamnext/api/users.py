from urllib.parse import unquote

import flask

from . import api_bp

from beetsplug.beetstreamnext.constants import ALLOWED_BITRATES
from beetsplug.beetstreamnext.user_management import (
    SAFE_USER_FIELDS, create_user, update_user, delete_user, get_userdata, load_all_users
)
from beetsplug.beetstreamnext.utils import subsonic_error, subsonic_response, api_bool, safe_str


def user_payload(user_data: dict) -> dict:
    """Build a Subsonic user dict from a load_userdata result."""
    return {
        'username':            user_data.get('username', ''),
        'email':               user_data.get('email', '') or '',
        'scrobblingEnabled':   bool(user_data.get('scrobblingEnabled', True)),
        'maxBitRate':          int(user_data.get('maxBitRate', 0)),
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
        # 'avatarLastChanged':   '',  # TODO
        'folder':              [0],     # Beets has only one 'folder'
    }


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getUser/
@api_bp.route('/getUser', methods=["GET", "POST"])
@api_bp.route('/getUser.view', methods=["GET", "POST"])
def endpoint_get_user():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    username = r.get('username', default=flask.g.username, type=safe_str)    # Required
    # (defaults to flask.g.username so non-admins can only query themselves)

    username = unquote(username)

    requesting_user_data = flask.g.user_data
    if not requesting_user_data:
        return subsonic_error(40, resp_fmt=resp_fmt)

    if username != flask.g.username and not requesting_user_data.get('adminRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if username != flask.g.username:
        target_data = get_userdata(username)
        if not target_data:
            return subsonic_error(70, resp_fmt=resp_fmt)
    else:
        target_data = requesting_user_data

    payload = {
        'user': user_payload(target_data)
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getUsers/
@api_bp.route('/getUsers', methods=["GET", "POST"])
@api_bp.route('/getUsers.view', methods=["GET", "POST"])
def endpoint_get_users():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    if not flask.g.user_data or not bool(flask.g.user_data.get('adminRole')):
        return subsonic_error(50, resp_fmt=resp_fmt)

    payload = {
        'users': {
            'user': [user_payload(u) for u in load_all_users()]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/createUser/
@api_bp.route('/createUser', methods=["GET", "POST"])
@api_bp.route('/createUser.view', methods=["GET", "POST"])
def endpoint_create_user():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    username = r.get('username', default='', type=safe_str)         # Required
    password = unquote(r.get('password', default='', type=str))     # Required
    # email = r.get('email', default='', type=safe_str)             # Required??? uhhh no thanks

    if not flask.g.user_data or not bool(flask.g.user_data.get('adminRole')):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not username or not password:
        return subsonic_error(10, resp_fmt=resp_fmt)

    try:
        params = {}
        for field in SAFE_USER_FIELDS:
            if field in r:
                if field == 'maxBitRate':
                    val = r.get(field, default=0, type=int)
                    params[field] = val if val in ALLOWED_BITRATES else 0
                else:
                    params[field] = int(r.get(field, default=False, type=api_bool))

        # Explicitly pull adminRole for create_user
        is_admin = params.pop('adminRole', False)
        create_user(username, password, admin=is_admin, **params)

        return subsonic_response({}, resp_fmt=resp_fmt)

    except ValueError as e:
        return subsonic_error(70, message=str(e), resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/updateUser/
@api_bp.route('/updateUser', methods=["GET", "POST"])
@api_bp.route('/updateUser.view', methods=["GET", "POST"])
def endpoint_update_user():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    username = r.get('username', default='', type=safe_str)     # Required
    password = unquote(r.get('password', default='', type=str))
    # email = r.get('email', default='', type=safe_str)

    if not flask.g.user_data or not bool(flask.g.user_data.get('adminRole')):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not username:
        return subsonic_error(10, message='Username is required.', resp_fmt=resp_fmt)

    try:
        updates = {}

        if password:
            updates['password'] = password

        if 'maxBitRate' in r:
            br = r.get('maxBitRate', default=0, type=int)
            updates['maxBitRate'] = br if br in ALLOWED_BITRATES else 0

        for field in SAFE_USER_FIELDS:
            if field in r and field not in ('password', 'maxBitRate'):
                updates[field] = int(r.get(field, default=False, type=api_bool))

        update_user(username, **updates)
        return subsonic_response({}, resp_fmt)

    except ValueError as e:
        return subsonic_error(70, message=str(e), resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/deleteUser/
@api_bp.route('/deleteUser', methods=["GET", "POST"])
@api_bp.route('/deleteUser.view', methods=["GET", "POST"])
def endpoint_delete_user():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    target_user = r.get('username', default='', type=safe_str)   # Required
    target_user = unquote(target_user)

    if not flask.g.user_data or not bool(flask.g.user_data.get('adminRole')):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not target_user:
        return subsonic_error(10, message='Username to be deleted must be passed.', resp_fmt=resp_fmt)

    if target_user == flask.g.username:
        return subsonic_error(50, message="Admins cannot delete their own account via this endpoint.", resp_fmt=resp_fmt)

    if delete_user(target_user):
        return subsonic_response({}, resp_fmt)

    return subsonic_error(70, message="User not found.", resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/changePassword/
@api_bp.route('/changePassword', methods=["GET", "POST"])
@api_bp.route('/changePassword.view', methods=["GET", "POST"])
def endpoint_change_password():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    target_user = r.get('username', default=flask.g.username, type=safe_str)     # Required
    new_password =  r.get('password', default='', type=str)                 # Required
    target_user = unquote(target_user)
    new_password = unquote(new_password)

    # User can change their own password, admin can change anyone's
    is_self = (target_user == flask.g.username)
    is_admin = flask.g.user_data.get('adminRole', False)

    if not is_self and not is_admin:
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not new_password:
        return subsonic_error(10, resp_fmt=resp_fmt)

    try:
        update_user(target_user, password=new_password)
        return subsonic_response({}, resp_fmt)
    except ValueError:
        return subsonic_error(70, resp_fmt=resp_fmt)
