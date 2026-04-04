import hashlib
import hmac
import secrets
from typing import TYPE_CHECKING, Union, Sequence, Optional, Dict
from urllib.parse import unquote

import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.db import get_cipher, database
from beetsplug.beetstreamnext.utils import subsonic_error, subsonic_response, api_bool, safe_str

if TYPE_CHECKING:
    from werkzeug.datastructures import CombinedMultiDict


def _user_payload(user_data: dict) -> dict:
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


_ALLOWED_USER_FIELDS = frozenset({
    'password', 'email', 'avatar', 'avatarLastChanged', 'scrobblingEnabled', 'adminRole', 'settingsRole',
    'streamRole', 'jukeboxRole', 'downloadRole', 'uploadRole', 'coverArtRole', 'playlistRole', 'commentRole',
    'podcastRole', 'shareRole', 'videoConversionRole', 'folder', 'maxBitRate'
})

_ALLOWED_BITRATES = frozenset({0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320})

# Dummies for constant-time comparison when username not found
_DUMMY_SALT = 'beetstreamnext_dummy_salt'
_DUMMY_TOKEN = hashlib.md5(f'beetstreamnext_dummy_password{_DUMMY_SALT}'.encode()).hexdigest()


##
# Internal helpers to this module


def _get_userdata(username: str, fields: Optional[Union[str, Sequence[str]]] = None) -> Optional[Dict]:

    if fields is None:
        # return all safe fields
        safe_fields = sorted(list(_ALLOWED_USER_FIELDS))
    elif isinstance(fields, str):
        safe_fields = [fields] if fields in _ALLOWED_USER_FIELDS else []
    else:
        safe_fields = sorted(list(set(fields).intersection(_ALLOWED_USER_FIELDS)))

    if not safe_fields:
        return None

    column_names = ['username'] + safe_fields
    columns_str = ', '.join(column_names)

    with database() as db:
        row = db.execute(
            f"""
            SELECT {columns_str}
            FROM users
            WHERE username = ?
            """, (username,)
        ).fetchone()

    if not row:
        return None

    user_dict = dict(zip(column_names, row))

    cipher = get_cipher()

    if 'password' in user_dict.keys():
        password = user_dict.pop('password')

        if cipher:
            user_dict['password'] = cipher.decrypt(password).decode("utf-8")
        else:
            user_dict['password'] = password.decode('utf-8') if isinstance(password, bytes) else password

    return user_dict


def _store_userdata(user_dict: Dict):

    _user_dict = dict(user_dict)
    username = _user_dict.pop('username', None)
    if not username:
        raise ValueError("User dict must have the 'username' key!")

    filtered_dict = {k: v for k, v in _user_dict.items() if k in _ALLOWED_USER_FIELDS}

    cipher = get_cipher()
    if 'password' in filtered_dict and cipher:
        filtered_dict['password'] = cipher.encrypt(filtered_dict['password'].encode("utf-8"))

    columns = ['username']
    placeholders = ['?']
    values = [username]
    updates = []

    for key, val in filtered_dict.items():
        columns.append(key)
        placeholders.append('?')
        values.append(val)
        updates.append(f"{key} = excluded.{key}")

    columns_str = ', '.join(columns)
    placeholders_str = ', '.join(placeholders)
    updates_str = ', '.join(updates)

    with database() as db:
        db.execute(
            f"""
            INSERT INTO users ({columns_str})
            VALUES ({placeholders_str})
            ON CONFLICT (username)
            DO UPDATE SET {updates_str}
            """, values
        )

##
# Core logic used by endpoints and CLI

def create_user(username, password, admin=False, **kwargs):
    """Core logic to create a user. Returns the raw API key."""

    if _get_userdata(username, fields=['adminRole']):   # any field, doesnt matter
        raise ValueError(f"Username '{username}' already exists.")

    filtered_roles = {
        k: v for k, v in kwargs.items()
        if k in _ALLOWED_USER_FIELDS and k != 'password'  # 'username' and 'password' are handled explicitly
    }

    raw_api_key = secrets.token_urlsafe(32)
    api_key_hash = hashlib.sha256(raw_api_key.encode('utf-8')).hexdigest()

    user_data = {
        'username': username,
        'password': password,   # store_userdata handles the encryption
        'adminRole': int(admin),
    }
    some_defaults = {
        'scrobblingEnabled': 1,
        'playlistRole': 1,
        'settingsRole': 1,
        'streamRole': 1,
        'commentRole': 1,
        'maxBitRate': 0
    }
    for k, v in some_defaults.items():
        user_data.setdefault(k, v)

    user_data.update(filtered_roles)
    _store_userdata(user_data)

    # Manually set api_key_hash because store_userdata excludes it for safety
    with database() as db:
        db.execute(
            """
            UPDATE users
            SET api_key_hash = ?
            WHERE username = ?""", (api_key_hash, username)
        )

    return raw_api_key


def update_user(username: str, **updates):
    """Core logic to update an existing user. """

    if not _get_userdata(username, fields=['adminRole']):   # any field, doesnt matter
        raise ValueError(f"User '{username}' does not exist.")

    filtered_updates = {k: v for k, v in updates.items() if k in _ALLOWED_USER_FIELDS}
    filtered_updates['username'] = username

    _store_userdata(filtered_updates)


def delete_user(username: str) -> bool:
    """Core logic to delete a user and related data."""
    with database() as db:
        cursor = db.execute(
            """
            DELETE
            FROM users
            WHERE username = ?
            """, (username,)
        )
    return cursor.rowcount > 0


##
# Endpoints

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getUser/
@app.route('/rest/getUser', methods=["GET", "POST"])
@app.route('/rest/getUser.view', methods=["GET", "POST"])
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
        target_data = _get_userdata(username)
        if not target_data:
            return subsonic_error(70, resp_fmt=resp_fmt)
    else:
        target_data = requesting_user_data

    payload = {
        'user': _user_payload(target_data)
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getUsers/
@app.route('/rest/getUsers', methods=["GET", "POST"])
@app.route('/rest/getUsers.view', methods=["GET", "POST"])
def endpoint_get_users():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    if not flask.g.user_data or not flask.g.user_data.get('adminRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    payload = {
        'users': {
            'user': [_user_payload(u) for u in load_all_users()]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/createUser/
@app.route('/rest/createUser', methods=["GET", "POST"])
@app.route('/rest/createUser.view', methods=["GET", "POST"])
def endpoint_create_user():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    username = r.get('username', default='', type=safe_str)         # Required
    password = unquote(r.get('password', default='', type=str))     # Required
    # email = r.get('email', default='', type=safe_str)             # Required??? uhhh no thanks

    if not flask.g.user_data or not flask.g.user_data.get('adminRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not username or not password:
        return subsonic_error(10, resp_fmt=resp_fmt)

    try:
        params = {}
        for field in _ALLOWED_USER_FIELDS:
            if field in r:
                if field == 'maxBitRate':
                    val = r.get(field, default=0, type=int)
                    params[field] = val if val in _ALLOWED_BITRATES else 0
                else:
                    params[field] = int(r.get(field, default=False, type=api_bool))

        # Explicitly pull adminRole for create_user
        is_admin = params.pop('adminRole', False)
        create_user(username, password, admin=is_admin, **params)

        return subsonic_response({}, resp_fmt=resp_fmt)

    except ValueError as e:
        return subsonic_error(70, message=str(e), resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/updateUser/
@app.route('/rest/updateUser', methods=["GET", "POST"])
@app.route('/rest/updateUser.view', methods=["GET", "POST"])
def endpoint_update_user():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    username = r.get('username', default='', type=safe_str)     # Required
    password = unquote(r.get('password', default='', type=str))
    # email = r.get('email', default='', type=safe_str)

    if not flask.g.user_data or not flask.g.user_data.get('adminRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not username:
        return subsonic_error(10, message='Username is required.', resp_fmt=resp_fmt)

    try:
        updates = {}

        if password:
            updates['password'] = password

        if 'maxBitRate' in r:
            br = r.get('maxBitRate', default=0, type=int)
            updates['maxBitRate'] = br if br in _ALLOWED_BITRATES else 0

        for field in _ALLOWED_USER_FIELDS:
            if field in r and field not in ('password', 'maxBitRate'):
                updates[field] = int(r.get(field, default=False, type=api_bool))

        update_user(username, **updates)
        return subsonic_response({}, resp_fmt)

    except ValueError as e:
        return subsonic_error(70, message=str(e), resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/deleteUser/
@app.route('/rest/deleteUser', methods=["GET", "POST"])
@app.route('/rest/deleteUser.view', methods=["GET", "POST"])
def endpoint_delete_user():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    target_user = r.get('username', default='', type=safe_str)   # Required
    target_user = unquote(target_user)

    if not flask.g.user_data or not flask.g.user_data.get('adminRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not target_user:
        return subsonic_error(10, message='Username to be deleted must be passed.', resp_fmt=resp_fmt)

    if target_user == flask.g.username:
        return subsonic_error(50, message="Admins cannot delete their own account via this endpoint.", resp_fmt=resp_fmt)

    if delete_user(target_user):
        return subsonic_response({}, resp_fmt)

    return subsonic_error(70, message="User not found.", resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/changePassword/
@app.route('/rest/changePassword', methods=["GET", "POST"])
@app.route('/rest/changePassword.view', methods=["GET", "POST"])
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


##
# Public functions used by other modules


def load_username(api_key_hash: str) -> str:
    with database() as db:
        row = db.execute(
            """
            SELECT username 
            FROM users 
            WHERE api_key_hash = ?
            """, (api_key_hash,)
        ).fetchone()
    return row[0] if row else None


def load_all_users() -> list[dict]:
    """Load roles/metadata for all users. Excludes password."""
    fields = list(_ALLOWED_USER_FIELDS - {'password'})
    columns = ['username'] + fields
    columns_str = ', '.join(columns)

    with database() as db:
        rows = db.execute(
            f"""
            SELECT {columns_str} 
            FROM users
            """
        ).fetchall()

    return [dict(zip(columns, row)) for row in rows]


def load_user_roles(username: str) -> Union[dict, None]:
    """Load all user fields except password, safe to cache in g."""
    return _get_userdata(username, fields=set(_ALLOWED_USER_FIELDS - {'password'}))


##

def authenticate(flask_req_values: 'CombinedMultiDict'):
    r = flask_req_values
    api_key = r.get('apiKey', default='', type=str)
    user = r.get('u', default='', type=safe_str)
    token = r.get('t', default='', type=str)
    salt = r.get('s', default='', type=str)
    clearpass = r.get('p', default='', type=str)

    api_key = unquote(api_key)
    user = unquote(user)
    token = unquote(token)
    salt = unquote(salt)
    clearpass = unquote(clearpass)

    # API Key (modern)
    if api_key:
        if user or token or salt or clearpass:
            return False, 43, None

        api_key_hash = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
        found_user = load_username(api_key_hash)
        if found_user:
            return True, 0, found_user
        return False, 40, None

    # Legacy (MD5 / password)
    else:
        if clearpass and (token or salt):
            return False, 43, None

        if not app.config.get('legacy_auth', True):
            return False, 42, None

        if not user:
            return False, 10, None

        user_data = _get_userdata(user, fields=['password'])
        if not user_data:
            _get_userdata('', fields=['password'])  # dummy DB round-trip for timing
            hmac.compare_digest(token or '', _DUMMY_TOKEN)
            return False, 40, None

        stored_password = user_data['password']
        if token and salt:
            expected = hashlib.md5(f"{stored_password}{salt}".encode('utf-8')).hexdigest().lower()
            if hmac.compare_digest(token, expected):
                return True, 0, user

        elif clearpass:
            if clearpass.startswith('enc:'):
                try:
                    decoded = bytes.fromhex(clearpass.removeprefix('enc:')).decode('utf-8')
                except ValueError:
                    return False, 40, None
                ok = hmac.compare_digest(decoded, stored_password)
            else:
                ok = hmac.compare_digest(clearpass, stored_password)
            if ok:
                return True, 0, user

    return False, 40, None

