import sqlite3
import os
from typing import Union
from cryptography.fernet import Fernet

# TODO - handle these correctly in the init and in a flask.g attribute
GLOBAL_ENCRYPTION_KEY = os.environ.get('BEETSTREAM_KEY')
cipher = Fernet(GLOBAL_ENCRYPTION_KEY)
DB_PATH = './beetstream-dev.db'


def initialise_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA foreign_keys = ON;")

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
            );
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

    for key, val in user_dict.items():
        if key == 'password':
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

    if 'password' in user_dict.keys():
        password = user_dict.pop('password')

        try:
            user_dict['password'] = cipher.decrypt(password).decode("utf-8")
        except Exception:
            # TODO - need to deal with exceptions correctly here
            pass

    return user_dict