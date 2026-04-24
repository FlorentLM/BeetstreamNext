import hashlib
import hmac
import secrets
from typing import TYPE_CHECKING, Sequence, Optional, Dict
from urllib.parse import unquote

from .application import app
from .db import get_cipher, database
from .utils import safe_str

if TYPE_CHECKING:
    from werkzeug.datastructures import CombinedMultiDict


SAFE_USER_FIELDS = frozenset({
    'password', 'email', 'avatar', 'avatarLastChanged', 'scrobblingEnabled', 'adminRole', 'settingsRole',
    'streamRole', 'jukeboxRole', 'downloadRole', 'uploadRole', 'coverArtRole', 'playlistRole', 'commentRole',
    'podcastRole', 'shareRole', 'videoConversionRole', 'folder', 'maxBitRate'
})

# Dummy password for constant-time comparison when username not found
_DUMMY_PASSWORD = secrets.token_urlsafe(12)


def get_userdata(username: str, fields: Optional[str | Sequence[str]] = None) -> Optional[Dict]:

    if fields is None:
        # return all safe fields
        safe_fields = sorted(list(SAFE_USER_FIELDS))
    elif isinstance(fields, str):
        safe_fields = [fields] if fields in SAFE_USER_FIELDS else []
    else:
        safe_fields = sorted(list(set(fields).intersection(SAFE_USER_FIELDS)))

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

    filtered_dict = {k: v for k, v in _user_dict.items() if k in SAFE_USER_FIELDS}

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

    if get_userdata(username, fields=['adminRole']):   # any field, doesnt matter
        raise ValueError(f"Username '{username}' already exists.")

    filtered_roles = {
        k: v for k, v in kwargs.items()
        if k in SAFE_USER_FIELDS and k != 'password'  # 'username' and 'password' are handled explicitly
    }

    raw_api_key = secrets.token_urlsafe(32)
    api_key_hash = hashlib.sha256(raw_api_key.encode('utf-8')).hexdigest()

    username = safe_str(username)
    password = unquote(password)

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

    if not get_userdata(username, fields=['adminRole']):   # any field, doesnt matter
        raise ValueError(f"User '{username}' does not exist.")

    filtered_updates = {k: v for k, v in updates.items() if k in SAFE_USER_FIELDS}
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
    fields = list(SAFE_USER_FIELDS - {'password'})
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


def load_user_roles(username: str) -> Optional[Dict]:
    """Load all user fields except password, safe to cache in g."""
    return get_userdata(username, fields=set(SAFE_USER_FIELDS - {'password'}))


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

    if token and len(token) < 32:
        token = token.zfill(32)  # some clients strip leading zeros...

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

        user_data = get_userdata(user, fields=['password'])
        if not user_data:
            get_userdata('', fields=['password'])  # dummy DB round-trip

            dummy_pw = _DUMMY_PASSWORD

            if token and salt:
                expected = hashlib.md5(f"{dummy_pw}{salt}".encode('utf-8')).hexdigest().lower()
                _ = hmac.compare_digest(token, expected)

            elif clearpass:
                if clearpass.startswith('enc:'):
                    try:
                        decoded = bytes.fromhex(clearpass.removeprefix('enc:')).decode('utf-8')
                    except ValueError:
                        return False, 40, None
                    _ = hmac.compare_digest(decoded, dummy_pw)
                else:
                    _ = hmac.compare_digest(clearpass, dummy_pw)

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

