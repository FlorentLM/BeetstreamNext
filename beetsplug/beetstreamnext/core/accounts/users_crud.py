from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from io import BytesIO
from typing import Sequence, Optional, Dict, Tuple, List
import sqlite3

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.accounts.user_schema import ALL_USER_FIELDS, PUBLIC_USER_FIELDS, USER_ROLES_SCHEMA
from beetsplug.beetstreamnext.core.storage.encryption import get_cipher
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.constants import MIN_PASSWORD_LEN


def get_userdata(username: str, fields: Optional[str | Sequence[str]] = None, include_password: bool = False) -> dict:
    
    existing_fields = set(ALL_USER_FIELDS) if include_password else set(ALL_USER_FIELDS) - {'password'}
    
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

    user_dict = dict(zip(column_names, row, strict=True))

    cipher = get_cipher()

    if 'password' in user_dict.keys():
        password = user_dict.pop('password')

        if cipher:
            user_dict['password'] = cipher.decrypt(password).decode("utf-8")
        else:
            user_dict['password'] = password.decode('utf-8') if isinstance(password, bytes) else password

    return user_dict


def _store_userdata(user_dict: dict, insert_only: bool = False, db: Optional[sqlite3.Connection] = None) -> None:
    """
    Insert or update a user row. If `db` is given, executes on that connection without
    opening/committing here (caller owns the transaction).
    """

    _user_dict = dict(user_dict)
    username = _user_dict.pop('username', None)
    if not username:
        raise ValueError("User dict must have the 'username' key!")

    filtered_dict = {k: v for k, v in _user_dict.items() if k in ALL_USER_FIELDS}

    cipher = get_cipher()
    if 'password' in filtered_dict and cipher:
        filtered_dict['password'] = cipher.encrypt(filtered_dict['password'].encode("utf-8"))

    if insert_only:
        # Creating a new user
        columns = ['username']
        placeholders = ['?']
        values = [username]

        for key, val in filtered_dict.items():
            columns.append(key)
            placeholders.append('?')
            values.append(val)

        columns_str = ', '.join(columns)
        placeholders_str = ', '.join(placeholders)

        sql = f"INSERT INTO users ({columns_str}) VALUES ({placeholders_str})"
        sql_values = values
    else:
        # Updating an existing user
        if not filtered_dict:
            return  # nothing to update

        update_clauses = []
        sql_values = []

        for key, val in filtered_dict.items():
            update_clauses.append(f"{key} = ?")
            sql_values.append(val)

        sql = f"UPDATE users SET {', '.join(update_clauses)} WHERE username = ?"
        sql_values.append(username)

    def _execute(conn: sqlite3.Connection) -> None:
        try:
            conn.execute(sql, sql_values)
        except sqlite3.IntegrityError as e:
            if insert_only:
                raise ValueError(f"Username '{username}' already exists.") from e
            raise

    if db is not None:
        _execute(db)
    else:
        with database() as conn:
            _execute(conn)

##
# Core logic used by endpoints and CLI

def _set_api_key(username: str, db: Optional[sqlite3.Connection] = None) -> str:
    """
    Generate a new API key for a user, store its hash, return the raw key.
    If `db` is given, executes on that connection without
    opening/committing here (caller owns the transaction).
    """
    raw_api_key = secrets.token_urlsafe(32)
    api_key_hash = hashlib.sha256(raw_api_key.encode('utf-8')).hexdigest()

    def _execute(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE users
            SET api_key_hash = ?
            WHERE username = ?
            """, (api_key_hash, username)
        )

    if db is not None:
        _execute(db)
    else:
        with database() as conn:
            _execute(conn)

    return raw_api_key


def regenerate_api_key(username: str) -> str:
    """Rotate a user's API key (invalidating the previous one), return the raw key."""
    if not get_userdata(username, fields=['username']):
        raise ValueError(f"User '{username}' does not exist.")
    return _set_api_key(username)


def create_user(username, password, admin=False, **kwargs):
    """Core logic to create a user. Returns the raw API key."""

    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f'Password must be at least {MIN_PASSWORD_LEN} characters.')

    username = safe_str(username)

    if not username or '/' in username or '\\' in username or username in ('.', '..'):
        raise ValueError("Username can't be empty, contain '/' or '\\', or be '.' or '..'.")

    if get_userdata(username, fields=['adminRole']):  # any field, doesn't matter
        raise ValueError(f"Username '{username}' already exists.")

    filtered_roles = {
        k: v for k, v in kwargs.items()
        if k in ALL_USER_FIELDS and k not in ('username', 'password')  # 'username' and 'password' are handled explicitly
    }

    user_data = {
        'username': username,
        'password': password,   # _store_userdata handles the encryption
        'adminRole': admin,
        'maxBitRate': 0,        # 'no limit'
    }

    for role_name, _, default_val in USER_ROLES_SCHEMA:
        if role_name not in user_data:
            user_data[role_name] = default_val

    user_data.update(filtered_roles)


    with database() as db:
        _store_userdata(user_data, insert_only=True, db=db)
        # api_key_hash is set separately because _store_userdata excludes it (for safety)
        raw_api_key = _set_api_key(username, db=db)

    return raw_api_key


def update_user(username: str, **updates):
    """Core logic to update an existing user. """

    if not get_userdata(username, fields=['username']):   # any field, doesnt matter
        raise ValueError(f"User '{username}' does not exist.")

    filtered_updates = {k: v for k, v in updates.items() if k in ALL_USER_FIELDS}
    filtered_updates['username'] = username

    if 'password' in filtered_updates and len(filtered_updates['password']) < MIN_PASSWORD_LEN:
        raise ValueError(f'Password must be at least {MIN_PASSWORD_LEN} characters.')

    _store_userdata(filtered_updates)


def delete_user(username: str) -> bool:
    """Core logic to delete a user and related data."""
    with database() as db:
        # chat_messages is deliberately not foreign-keyed to users
        db.execute("DELETE FROM chat_messages WHERE username = ?", (username,))
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


def list_users(fields: Optional[Sequence[str]] = None) -> List[Dict]:
    """
    Load roles/metadata for all users. Defaults to public user fields only.
    """
    if fields is None:
        column_names = sorted(list(PUBLIC_USER_FIELDS))
    else:
        column_names = sorted(list(set(fields).intersection(ALL_USER_FIELDS)))

    if not column_names:
        return []

    columns_str = ', '.join(column_names)

    with database() as db:
        rows = db.execute(
            f"""SELECT {columns_str} FROM users"""
        ).fetchall()

    return [dict(zip(column_names, row, strict=True)) for row in rows]


def session_stamp(username: str) -> Optional[str]:
    """
    Stamp of a user's current password: a session ith non-matching stamp is rejected.
    """
    password = get_userdata(username, fields='password', include_password=True).get('password')
    if password is None:
        return None
    key = str(app.config['SECRET_KEY']).encode('utf-8')   # Keyed with the session secret so the cookie reveals nothing
    return hmac.new(key, password.encode('utf-8'), hashlib.sha256).hexdigest()[:32]


def get_user_roles(username: str) -> dict:
    """Load all user fields except password, safe to cache in g."""
    return get_userdata(username)


def set_user_avatar(username: str, blob: Optional[bytes | BytesIO] = None) -> bool:
    """Updates a user's avatar. If blob is None, deletes the avatar. Returns True if user found."""

    if isinstance(blob, BytesIO):
        blob = blob.read()

    with database() as db:
        cur = db.execute(
            """
            UPDATE users
            SET avatar = ?,
                avatarLastChanged = ?
            WHERE username = ?
            """, (blob, time.time() if blob else None, username)
        )
    return cur.rowcount > 0


def get_user_avatar(username: str) -> Tuple[Optional[bytes], Optional[float]]:
    """Returns (blob_bytes, last_changed_timestamp)."""
    with database() as db:
        row = db.execute(
            """
            SELECT avatar, avatarLastChanged 
            FROM users 
            WHERE username = ?
            """, (username,)
        ).fetchone()
    if row and row['avatar']:
        return row['avatar'], row['avatarLastChanged']
    return None, None
