import sqlite3
import os
import base64
import hashlib
from pathlib import Path
from typing import Union
from cryptography.fernet import Fernet

# TODO - handle these correctly in the init and in a flask.g attribute

DB_PATH = './beetstream-dev.db'


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
    key = os.environ.get('BEETSTREAM_KEY')
    try:
        cipher = Fernet(key)
    except ValueError:
        cipher = None
    return cipher


def get_key_hash() -> Union[str, None]:
    load_env_file()
    key = os.environ.get('BEETSTREAM_KEY')
    if not key:
        return None
    decoded_key = base64.urlsafe_b64decode(key)
    return hashlib.sha256(decoded_key).hexdigest()


def verify_key():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    result = cur.execute("SELECT value FROM encryption WHERE key = 'key_hash'").fetchone()
    conn.close()

    stored_hash = result[0] if result else None
    current_hash = get_key_hash()

    return current_hash == stored_hash


def initialise_db():
    conn = sqlite3.connect(DB_PATH)
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

        cur.execute(f"""
                    INSERT INTO encryption (key, value) VALUES ('enabled', 'true');
                """)

        cur.execute("""
                        INSERT OR REPLACE INTO encryption (key, value) VALUES (?, ?)
                    """, ('key_hash', key_hash))
    else:
        cur.execute(f"""
                            INSERT INTO encryption (key, value) VALUES ('enabled', 'false');
                        """)

        cur.execute("""
                        INSERT OR REPLACE INTO encryption (key, value) VALUES (?, ?)
                    """, ('key_hash', None))

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password BLOB NOT NULL,
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


def store_userdata(user_dict):

    username = user_dict.pop("username", None)
    if not username:
        raise ValueError('User dict must have the "username" key!')

    columns = ['username']
    placeholders = ['?']
    updates = []
    values = [username]

    cipher = get_cipher()

    for key, val in user_dict.items():
        if cipher:
            val = cipher.encrypt(val.encode("utf-8"))
        columns.append(key)
        placeholders.append('?')
        updates.append(f"{key} = excluded.{key}")
        values.append(1 if val else 0)

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

    conn = sqlite3.connect(DB_PATH)
    conn.execute(sql, values)
    conn.commit()
    conn.close()


def load_userdata(username: str, fields: Union[list[str], tuple[str], set[str], str, None] = None) -> Union[dict, None]:

    if fields is None:
        return None

    elif isinstance(fields, str):
        fields = {fields}
    else:
        fields = set(fields)

    # We don't really want SQL injection :)
    safe_fields = list(set(fields).intersection(
        {'password', 'email', 'avatar', 'avatarLastChanged', 'scrobblingEnabled', 'adminRole', 'settingsRole',
         'streamRole', 'jukeboxRole', 'downloadRole', 'uploadRole', 'coverArtRole', 'playlistRole', 'commentRole',
         'podcastRole', 'shareRole', 'videoConversionRole', 'folder', 'maxBitRate'}
    ))

    if not safe_fields:
        return None

    columns_str = "username, " + ", ".join(safe_fields)

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(f"""
            SELECT {columns_str}
              FROM users
             WHERE username = ?
        """, (username,)).fetchone()
    conn.close()

    if not row:
        return None

    user_dict = {k: v for k, v in zip(columns_str, row)}

    cipher = get_cipher()

    if 'password' in user_dict.keys():
        password = user_dict.pop('password')

        if cipher:
            user_dict['password'] = cipher.decrypt(password).decode("utf-8")
        else:
            user_dict['password'] = password

    return user_dict