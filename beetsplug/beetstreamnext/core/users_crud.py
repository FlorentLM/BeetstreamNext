import hashlib
import hmac
import secrets
from typing import TYPE_CHECKING, Sequence, Optional, Dict, Tuple, List

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import EXISTING_USER_FIELDS
from .db import get_cipher, database
from beetsplug.beetstreamnext.utils import safe_str

if TYPE_CHECKING:
    from werkzeug.datastructures import CombinedMultiDict


# Dummy strings comparison when username not found
_DUMMY_PASSWORD = secrets.token_urlsafe(24)
_DUMMY_TOKEN: Optional[bytes] = None   # set lazily (cipher may not exist yet)

def _dummy_stored_password() -> str:
    """Decrypt a dummy Fernet token to mimic the cost of real password retrieval."""
    global _DUMMY_TOKEN
    cipher = get_cipher()
    if cipher is None:
        return _DUMMY_PASSWORD
    if _DUMMY_TOKEN is None:
        _DUMMY_TOKEN = cipher.encrypt(_DUMMY_PASSWORD.encode('utf-8'))
    try:
        return cipher.decrypt(_DUMMY_TOKEN).decode('utf-8')
    except Exception:
        return _DUMMY_PASSWORD


def get_userdata(username: str, fields: Optional[str | Sequence[str]] = None, include_password: bool = False) -> Dict:
    
    existing_fields = set(EXISTING_USER_FIELDS) if include_password else set(EXISTING_USER_FIELDS) - {'password'}
    
    if fields is None:
        # return all safe fields
        column_names = sorted(list(existing_fields))
    elif isinstance(fields, str):
        column_names = [fields] if fields in existing_fields else []
    else:
        column_names = sorted(list(set(fields).intersection(existing_fields)))

    if not column_names:
        return {}

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
        return {}

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

    filtered_dict = {k: v for k, v in _user_dict.items() if k in EXISTING_USER_FIELDS}

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

def _set_api_key(username: str) -> str:
    """Generate a new API key for a user, store its hash, return the raw key."""
    raw_api_key = secrets.token_urlsafe(32)
    api_key_hash = hashlib.sha256(raw_api_key.encode('utf-8')).hexdigest()

    with database() as db:
        db.execute(
            """
            UPDATE users
            SET api_key_hash = ?
            WHERE username = ?
            """, (api_key_hash, username)
        )
    return raw_api_key


def regenerate_api_key(username: str) -> str:
    """Rotate a user's API key (invalidating the previous one), return the raw key."""
    if not get_userdata(username, fields=['username']):
        raise ValueError(f"User '{username}' does not exist.")
    return _set_api_key(username)


def create_user(username, password, admin=False, **kwargs):
    """Core logic to create a user. Returns the raw API key."""

    if get_userdata(username, fields=['adminRole']):   # any field, doesn't matter
        raise ValueError(f"Username '{username}' already exists.")

    filtered_roles = {
        k: v for k, v in kwargs.items()
        if k in EXISTING_USER_FIELDS and k not in ('username', 'password')  # 'username' and 'password' are handled explicitly
    }

    username = safe_str(username)

    user_data = {
        'username': username,
        'password': password,   # store_userdata handles the encryption
        'adminRole': admin,

        # Some defaults
        'scrobblingEnabled': True,
        'playlistRole': True,
        'settingsRole': True,
        'streamRole': True,
        'commentRole': True,
        'maxBitRate': False
    }

    user_data.update(filtered_roles)
    _store_userdata(user_data)

    # api_key_hash is set separately because _store_userdata excludes it (for safety)
    return _set_api_key(username)


def update_user(username: str, **updates):
    """Core logic to update an existing user. """

    if not get_userdata(username, fields=['username']):   # any field, doesnt matter
        raise ValueError(f"User '{username}' does not exist.")

    filtered_updates = {k: v for k, v in updates.items() if k in EXISTING_USER_FIELDS}
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


def load_all_users() -> List[Dict]:
    """Load roles/metadata for all users. Explicitly excludes password."""

    columns = sorted(list(EXISTING_USER_FIELDS - {'password'}))
    columns_str = ', '.join(columns)

    with database() as db:
        rows = db.execute(
            f"""
            SELECT {columns_str} 
            FROM users
            """
        ).fetchall()

    return [dict(zip(columns, row)) for row in rows]


def load_user_roles(username: str) -> Dict:
    """Load all user fields except password, safe to cache in g."""
    return get_userdata(username)


##

def _check_password(
        username: str,
        token: Optional[str] = None,
        salt: Optional[str] = None,
        clearpass: Optional[str] = None
    ) -> Tuple[bool, int, Optional[str]]:

    stored_password = get_userdata(username, fields=['password'], include_password=True).get('password')
    if not stored_password:
        stored_password = _dummy_stored_password()
        user_found = False
    else:
        user_found = True

    ok = False
    if token and salt:
        expected = hashlib.md5(f"{stored_password}{salt}".encode('utf-8')).hexdigest().lower()
        ok = hmac.compare_digest(token, expected)

    elif clearpass:
        if clearpass.startswith('enc:'):
            try:
                decoded = bytes.fromhex(clearpass.removeprefix('enc:')).decode('utf-8')
                ok = hmac.compare_digest(decoded, stored_password)
            except ValueError:
                ok = hmac.compare_digest(clearpass, stored_password)
        else:
            ok = hmac.compare_digest(clearpass, stored_password)

    if ok and user_found:
        return True, 0, username

    # 40: "Wrong username or password."
    return False, 40, None


def authenticate(flask_req_values: 'CombinedMultiDict'):
    r = flask_req_values
    api_key = r.get('apiKey', default='', type=str)
    user = r.get('u', default='', type=safe_str)
    token = r.get('t', default='', type=str)
    salt = r.get('s', default='', type=str)
    clearpass = r.get('p', default='', type=str)

    if token and len(token) < 32:
        token = token.zfill(32)  # some clients strip leading zeros...

    # API Key (modern)
    if api_key:
        if user or token or salt or clearpass:
            # 43: "Multiple conflicting authentication mechanisms provided."
            return False, 43, None

        api_key_hash = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
        found_user = load_username(api_key_hash)
        if found_user:
            return True, 0, found_user

        # 40: "Wrong username or password."
        return False, 40, None

    # Legacy (MD5 / password)
    else:
        if clearpass and (token or salt):
            # 43: "Multiple conflicting authentication mechanisms provided."
            return False, 43, None

        if not app.config.get('legacy_auth', True):
            # 42: "Provided authentication mechanism not supported."
            return False, 42, None

        if not user:
            # 10: "Required parameter is missing."
            return False, 10, None
        
        success, code, username = _check_password(user, token, salt, clearpass)
        return success, code, username