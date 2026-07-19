import flask

from .. import api_bp

from beetsplug.beetstreamnext.schemas import ALLOWED_BITRATES, USER_ROLES_SCHEMA
from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.images import sniff_image
from beetsplug.beetstreamnext.core.users_crud import (
    create_user, update_user, delete_user, get_userdata, load_all_users, get_user_avatar
)


def user_payload(user_data: dict) -> dict:
    """Build a OpenSubsonic user dict."""

    payload = {
        'username': user_data.get('username', ''),
        'email': user_data.get('email') or '',
        'maxBitRate': int(user_data.get('maxBitRate', 0)),
        'folder': [0],  # Beets has only one 'folder'
    }

    for field_name, _, _ in USER_ROLES_SCHEMA:
        payload[field_name] = bool(user_data.get(field_name, False))

    last_changed = user_data.get('avatarLastChanged')
    if last_changed:
        payload['avatarLastChanged'] = int(last_changed * 1000)

    return payload


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getUser/
@api_bp.route('/getUser', methods=['GET', 'POST'])
@api_bp.route('/getUser.view', methods=['GET', 'POST'])
def endpoint_get_user() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    username = r.get('username', default=flask.g.username, type=safe_str)    # Required
    # (defaults to flask.g.username so non-admins can only query themselves)

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
@api_bp.route('/getUsers', methods=['GET', 'POST'])
@api_bp.route('/getUsers.view', methods=['GET', 'POST'])
def endpoint_get_users() -> flask.Response:
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
@api_bp.route('/createUser', methods=['GET', 'POST'])
@api_bp.route('/createUser.view', methods=['GET', 'POST'])
def endpoint_create_user() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    username = r.get('username', default='', type=safe_str)         # Required
    password = r.get('password', default='', type=str)              # Required

    if not flask.g.user_data or not bool(flask.g.user_data.get('adminRole')):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not username or not password:
        return subsonic_error(10, resp_fmt=resp_fmt)

    try:
        params = {}
        if 'maxBitRate' in r:
            br = r.get('maxBitRate', default=0, type=int)
            params['maxBitRate'] = br if br in ALLOWED_BITRATES else 0

        if 'email' in r:
            params['email'] = r.get('email', type=safe_str)

        for role_name, _, _ in USER_ROLES_SCHEMA:
            if role_name in r:
                params[role_name] = api_bool(r.get(role_name))

        # Explicitly pull adminRole for create_user
        is_admin = params.pop('adminRole', False)
        create_user(username, password, admin=is_admin, **params)

        return subsonic_response({}, resp_fmt=resp_fmt)

    except ValueError as e:
        return subsonic_error(70, message=str(e), resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/updateUser/
@api_bp.route('/updateUser', methods=['GET', 'POST'])
@api_bp.route('/updateUser.view', methods=['GET', 'POST'])
def endpoint_update_user() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    username = r.get('username', default=r.get('u', ''), type=safe_str)     # Required
    password = r.get('password', default='', type=str)

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

        if 'email' in r:
            updates['email'] = r.get('email', type=safe_str)

        for role_name, _, _ in USER_ROLES_SCHEMA:
            if role_name in r:
                val = api_bool(r.get(role_name))

                if role_name == 'adminRole' and username == flask.g.username and val is False:
                    return subsonic_error(50, message='You cannot revoke your own admin status.', resp_fmt=resp_fmt)

                updates[role_name] = val

        update_user(username, **updates)
        return subsonic_response({}, resp_fmt)

    except ValueError as e:
        return subsonic_error(70, message=str(e), resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/deleteUser/
@api_bp.route('/deleteUser', methods=['GET', 'POST'])
@api_bp.route('/deleteUser.view', methods=['GET', 'POST'])
def endpoint_delete_user() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    target_user = r.get('username', default='', type=safe_str)   # Required

    if not flask.g.user_data or not bool(flask.g.user_data.get('adminRole')):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not target_user:
        return subsonic_error(10, message='Username to be deleted must be passed.', resp_fmt=resp_fmt)

    if target_user == flask.g.username:
        return subsonic_error(50, message='Admins cannot delete their own account via this endpoint.', resp_fmt=resp_fmt)

    if delete_user(target_user):
        return subsonic_response({}, resp_fmt)

    return subsonic_error(70, message="User not found.", resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/changePassword/
@api_bp.route('/changePassword', methods=['GET', 'POST'])
@api_bp.route('/changePassword.view', methods=['GET', 'POST'])
def endpoint_change_password() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    target_user = r.get('username', default=flask.g.username, type=safe_str)    # Required
    new_password =  r.get('password', default='', type=str)                     # Required

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


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getAvatar/
@api_bp.route('/getAvatar', methods=['GET', 'POST'])
@api_bp.route('/getAvatar.view', methods=['GET', 'POST'])
def endpoint_get_avatar() -> flask.Response:
    username = flask.request.args.get('username', default='', type=safe_str)    # Required
    if not username:
        return subsonic_error(10)

    blob, last_changed = get_user_avatar(username)
    if not blob:
        flask.abort(404)

    mimetype = sniff_image(blob) or 'image/jpeg'

    response = flask.make_response(blob)
    response.headers.set('Content-Type', mimetype)
    response.headers.set('Cache-Control', 'public, max-age=86400')  # 1 day cached
    return response
