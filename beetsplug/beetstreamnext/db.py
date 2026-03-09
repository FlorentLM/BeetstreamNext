import secrets
import sqlite3
import os
import base64
import hashlib
from pathlib import Path
from typing import Union

import flask
from cryptography.fernet import Fernet


def create_user(username, password, admin=False):
    """Creates a user, hashes new API key, returns API key."""
    raw_api_key = secrets.token_urlsafe(32)
    api_key_hash = hashlib.sha256(raw_api_key.encode('utf-8')).hexdigest()

    # Encrypt password for legacy Subsonic MD5 support
    cipher = get_cipher()
    encrypted_pw = cipher.encrypt(password.encode('utf-8')) if cipher else password.encode('utf-8')

    try:
        with sqlite3.connect(flask.current_app.config['DB_PATH']) as conn:
            conn.execute("""
                         INSERT INTO users (username, password, api_key_hash, adminRole, playlistRole, settingsRole)
                         VALUES (?, ?, ?, ?, 1, 1)
                         """, (username, encrypted_pw, api_key_hash, 1 if admin else 0))
    except sqlite3.IntegrityError as e:
        if 'UNIQUE' in str(e):
            raise ValueError(f"Username '{username}' already exists.") from e
        raise

    return raw_api_key


def load_env_file(filepath: Union[Path, str] = ".env") -> None:
    env_file = Path(filepath)

    if not env_file.is_file():
        return

    for line in env_file.read_text().splitlines():
        line = line.strip()

        if not line or line.startswith("#") or '=' not in line:
            continue

        var, value = line.split('=', 1)
        os.environ[var.strip()] = value.strip().strip('"').strip("'")


def get_cipher() -> Union[Fernet, None]:
    load_env_file()
    key = os.environ.get('BEETSTREAMNEXT_KEY')
    if not key:
        return None
    try:
        return Fernet(key)
    except Exception:
        return None


def get_key_hash() -> Union[str, None]:
    load_env_file()
    key = os.environ.get('BEETSTREAMNEXT_KEY')
    if not key:
        return None
    decoded_key = base64.urlsafe_b64decode(key)
    return hashlib.sha256(decoded_key).hexdigest()


def verify_key():
    conn = sqlite3.connect(flask.current_app.config['DB_PATH'])
    cur = conn.cursor()
    result = cur.execute("SELECT value FROM encryption WHERE key = 'key_hash'").fetchone()
    conn.close()

    stored_hash = result[0] if result else None
    current_hash = get_key_hash()

    return current_hash == stored_hash


def initialise_db():
    conn = sqlite3.connect(flask.current_app.config['DB_PATH'])
    cur = conn.cursor()

    cur.execute("PRAGMA foreign_keys = ON;")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS encryption (
            key TEXT PRIMARY KEY,
            value TEXT)
        """)

    cipher = get_cipher()

    if cipher is not None:
        key_hash = get_key_hash()

        cur.execute("""
                    INSERT OR IGNORE INTO encryption (key, value) VALUES ('enabled', 'true');
                """)

        cur.execute("""
                        INSERT OR REPLACE INTO encryption (key, value) VALUES (?, ?)
                    """, ('key_hash', key_hash))
    else:
        cur.execute("""
                            INSERT OR IGNORE INTO encryption (key, value) VALUES ('enabled', 'false');
                        """)

        cur.execute("""
                        INSERT OR REPLACE INTO encryption (key, value) VALUES (?, ?)
                    """, ('key_hash', None))

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password BLOB NOT NULL,
            api_key_hash TEXT UNIQUE,
            email TEXT,
            avatar BLOB,
            avatarLastChanged REAL,
            scrobblingEnabled INTEGER DEFAULT 0,
            adminRole INTEGER DEFAULT 0,
            settingsRole INTEGER DEFAULT 1,
            streamRole INTEGER DEFAULT 0,
            jukeboxRole INTEGER DEFAULT 0,
            downloadRole INTEGER DEFAULT 0,
            uploadRole INTEGER DEFAULT 0,
            coverArtRole INTEGER DEFAULT 0,
            playlistRole INTEGER DEFAULT 1,
            commentRole INTEGER DEFAULT 1,
            podcastRole INTEGER DEFAULT 0,
            shareRole INTEGER DEFAULT 0,
            videoConversionRole INTEGER DEFAULT 0,
            folder INTEGER DEFAULT 0,
            maxBitRate INTEGER DEFAULT 0        -- 0 (no limit), 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320
        )
    """)

    cur.execute("""
            CREATE TABLE IF NOT EXISTS likes (
                username   TEXT NOT NULL,
                item_type  TEXT NOT NULL,
                item_id    INTEGER NOT NULL,
                PRIMARY KEY (username, item_type, item_id),
                FOREIGN KEY (username) REFERENCES users (username)
        )
    """)

    cur.execute("""
            CREATE TABLE IF NOT EXISTS bookmarks (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT NOT NULL,
                song_id   INTEGER NOT NULL,
                position  REAL NOT NULL,
                comments  TEXT,
                FOREIGN KEY (username) REFERENCES users (username)
            )
        """)

    conn.commit()
    conn.close()

    # TODO: Could support key rotation and eventually encryption of a clear db? But that's probably not worth the hassle
    if cipher is not None and not verify_key():
        raise RuntimeError(
            "BEETSTREAMNEXT_KEY has changed since the database was initialised. Stored passwords are unrecoverable with the current key. "
            f"\nRestore the original key, or delete the database (`{flask.current_app.config['DB_PATH']}`) and run initial setup again."
        )


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
    placeholders =['?']
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

    sql = f"""
        INSERT INTO users ({columns_str})
        VALUES ({placeholders_str})
        ON CONFLICT (username)
        DO UPDATE SET
            {updates_str}
        """

    conn = sqlite3.connect(flask.current_app.config['DB_PATH']) # Note: Consider passing the path dynamically from Flask config!
    conn.execute(sql, values)
    conn.commit()
    conn.close()


_ALL_USER_FIELDS = frozenset({
    'password', 'email', 'avatar', 'avatarLastChanged', 'scrobblingEnabled', 'adminRole', 'settingsRole',
    'streamRole', 'jukeboxRole', 'downloadRole', 'uploadRole', 'coverArtRole', 'playlistRole', 'commentRole',
    'podcastRole', 'shareRole', 'videoConversionRole', 'folder', 'maxBitRate'
})


def load_userdata(username: str, fields: Union[list[str], tuple[str], set[str], str, None] = None) -> Union[dict, None]:

    if fields is None:
        # return all safe fields
        safe_fields = list(_ALL_USER_FIELDS)
    elif isinstance(fields, str):
        safe_fields = [fields] if fields in _ALL_USER_FIELDS else []
    else:
        # We don't really want SQL injection :)
        safe_fields = list(set(fields).intersection(_ALL_USER_FIELDS))

    if not safe_fields:
        return None

    column_names = ['username'] + safe_fields
    columns_str = ', '.join(column_names)

    conn = sqlite3.connect(flask.current_app.config['DB_PATH'])
    row = conn.execute(f"""
            SELECT {columns_str}
              FROM users
             WHERE username = ?
        """, (username,)).fetchone()
    conn.close()

    if not row:
        return None

    user_dict = dict(zip(column_names, row))

    cipher = get_cipher()

    if 'password' in user_dict.keys():
        password = user_dict.pop('password')

        if cipher:
            user_dict['password'] = cipher.decrypt(password).decode("utf-8")
        else:
            user_dict['password'] = password

    return user_dict