import hashlib
import hmac
import secrets
from sqlite3 import IntegrityError
from typing import Union, Sequence, Optional, Dict
from urllib.parse import unquote

import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.db import get_cipher, database
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


_ALL_USER_FIELDS = frozenset({
    'password', 'email', 'avatar', 'avatarLastChanged', 'scrobblingEnabled', 'adminRole', 'settingsRole',
    'streamRole', 'jukeboxRole', 'downloadRole', 'uploadRole', 'coverArtRole', 'playlistRole', 'commentRole',
    'podcastRole', 'shareRole', 'videoConversionRole', 'folder', 'maxBitRate'
})


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

    if not flask.g.user_data or not flask.g.user_data.get('adminRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    users = [_user_payload(u) for u in load_all_users()]
    payload = {'users': {'user': users}}
    return subsonic_response(payload, resp_fmt)


def create_user(username, password, admin=False):
    """Creates a user, hashes new API key, returns API key."""
    raw_api_key = secrets.token_urlsafe(32)
    api_key_hash = hashlib.sha256(raw_api_key.encode('utf-8')).hexdigest()

    # Encrypt password for legacy Subsonic MD5 support
    cipher = get_cipher()
    encrypted_pw = cipher.encrypt(password.encode('utf-8')) if cipher else password.encode('utf-8')

    try:
        with database() as db:
            db.execute(
                """
                INSERT INTO users (username, password, api_key_hash, adminRole, playlistRole, settingsRole)
                VALUES (?, ?, ?, ?, 1, 1)
                """, (username, encrypted_pw, api_key_hash, 1 if admin else 0)
            )
    except IntegrityError as e:
        if 'UNIQUE' in str(e):
            raise ValueError(f"Username '{username}' already exists.") from e
        raise

    return raw_api_key


##

def get_username(api_key_hash: str) -> str:
    row = database().execute("""SELECT username FROM users WHERE api_key_hash = ?""", (api_key_hash,)).fetchone()
    return row[0] if row else None


def load_all_users() -> list[dict]:
    """Load roles/metadata for all users. Excludes password."""
    fields = list(_ALL_USER_FIELDS - {'password'})
    columns = ['username'] + fields
    columns_str = ', '.join(columns)

    with database() as db:
        rows = db.execute(f"""SELECT {columns_str} FROM users""").fetchall()

    return [dict(zip(columns, row)) for row in rows]


def load_user_roles(username: str) -> Union[dict, None]:
    """Load all user fields except password, safe to cache in g."""
    return load_userdata(username, fields=set(_ALL_USER_FIELDS - {'password'}))


def load_user_likes(username: str) -> dict:
    """Load all likes for a user as {subsonic_id: starred_at}."""

    with database() as db:
        rows = db.execute(
            """
            SELECT item_id, starred_at
            FROM likes
            WHERE username = ?
            """, (username,)
        ).fetchall()

    likes = {item_id: starred_at for item_id, starred_at in rows}
    return likes


def load_user_ratings(username: str) -> dict:
    """Load all ratings for a user as {subsonic_id: starred_at}."""

    with database() as db:
        rows = db.execute(
            """
            SELECT item_id, rating
            FROM ratings
            WHERE username = ?
            """, (username,)
        ).fetchall()

    ratings = {item_id: rating for item_id, rating in rows}
    return ratings


def load_user_play_stats(username: str) -> dict:
    """Load play stats for a user as {beets_song_id: {'play_count': N, 'last_played': ts}}."""

    with database() as db:
        rows = db.execute(
            """
            SELECT song_id, play_count, last_played
            FROM play_stats
            WHERE username = ?
            """, (username,)
        ).fetchall()

    play_stats = {
        song_id: {'play_count': play_count, 'last_played': last_played}
        for song_id, play_count, last_played in rows
    }

    return play_stats


def load_userdata(username: str, fields: Optional[Union[str, Sequence[str]]] = None) -> Optional[Dict]:

    if fields is None:
        # return all safe fields
        safe_fields = sorted(list(_ALL_USER_FIELDS))
    elif isinstance(fields, str):
        safe_fields = [fields] if fields in _ALL_USER_FIELDS else []
    else:
        safe_fields = sorted(list(set(fields).intersection(_ALL_USER_FIELDS)))

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
            user_dict['password'] = password.decode('utf-8')

    return user_dict


def store_userdata(user_dict):
    user_dict = user_dict.copy()
    username = user_dict.pop("username", None)
    if not username:
        raise ValueError('User dict must have the "username" key!')

    safe_fields = {
        'password', 'email', 'avatar', 'avatarLastChanged', 'scrobblingEnabled',
        'adminRole', 'settingsRole', 'streamRole', 'jukeboxRole', 'downloadRole',
        'uploadRole', 'coverArtRole', 'playlistRole', 'commentRole', 'podcastRole',
        'shareRole', 'videoConversionRole', 'folder', 'maxBitRate'
    }
    filtered_dict = {k: v for k, v in user_dict.items() if k in safe_fields}


    cipher = get_cipher()
    if 'password' in filtered_dict and cipher:
        filtered_dict['password'] = cipher.encrypt(filtered_dict['password'].encode("utf-8"))

    columns = ['username']
    placeholders = ['?']
    updates = []
    values = [username]

    for key, val in filtered_dict.items():
        columns.append(key)
        placeholders.append('?')
        updates.append(f"{key} = excluded.{key}")
        values.append(val)

    columns_str = ', '.join(columns)
    placeholders_str = ', '.join(['?'] * len(columns))
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


def authenticate(req_values):
    api_key = unquote(req_values.get('apiKey', ''))
    user = unquote(req_values.get('u', ''))
    token = unquote(req_values.get('t', ''))
    salt = unquote(req_values.get('s', ''))
    clearpass = unquote(req_values.get('p', ''))

    legacy_auth_enabled = app.config.get('legacy_auth', True)

    # API Key (modern)
    if api_key:
        if user or token or salt or clearpass:
            return False, 43, None

        api_key_hash = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
        found_user = get_username(api_key_hash)
        if found_user:
            return True, 0, found_user
        return False, 40, None

    if not legacy_auth_enabled:
        return False, 42, None

    # Legacy (MD5 / password)
    if not user:
        return False, 10, None

    user_data = load_userdata(user, fields=['password'])
    if not user_data:
        # Dummy query + dummy comparison to keep response time constant regardless if username exists or not
        load_userdata('', fields=['password'])
        hmac.compare_digest('dummy', 'comparison')
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
