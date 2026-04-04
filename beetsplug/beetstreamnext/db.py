import sqlite3
import os
import base64
import hashlib
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from pathlib import Path
from typing import Union

import flask
from flask import g, current_app


def _load_env():
    try:
        db_dir = Path(flask.current_app.config['DB_PATH']).parent
        env_path = db_dir / '.env'
        if env_path.exists():
            load_dotenv(dotenv_path=env_path)
        else:
            load_dotenv()
    except (RuntimeError, KeyError):
        load_dotenv()


def get_cipher() -> Union[Fernet, None]:
    _load_env()
    key = os.environ.get('BEETSTREAMNEXT_KEY')
    if not key:
        return None
    try:
        return Fernet(key)
    except Exception:
        return None


def get_key_hash() -> Union[str, None]:
    _load_env()
    key = os.environ.get('BEETSTREAMNEXT_KEY')
    if not key:
        return None
    decoded_key = base64.urlsafe_b64decode(key)
    return hashlib.sha256(decoded_key).hexdigest()


def verify_key():

    with database() as db:
        result = db.execute("""SELECT value FROM encryption WHERE key = 'key_hash'""").fetchone()

    stored_hash = result[0] if result else None
    current_hash = get_key_hash()

    return current_hash == stored_hash


def initialise_db():
    conn = sqlite3.connect(flask.current_app.config['DB_PATH'])
    cur = conn.cursor()

    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")
    cur.execute("PRAGMA foreign_keys = ON;")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS encryption (
            key TEXT PRIMARY KEY,
            value TEXT)
        """
    )

    cipher = get_cipher()

    if cipher is not None:
        key_hash = get_key_hash()

        cur.execute(
            """
            INSERT OR IGNORE INTO encryption (key, value)
            VALUES ('enabled', 'true');
            """
        )

        cur.execute(
            """
            INSERT OR REPLACE INTO encryption (key, value) VALUES (?, ?)
            """, ('key_hash', key_hash)
        )

    else:
        cur.execute(
            """
            INSERT OR IGNORE INTO encryption (key, value)
            VALUES ('enabled', 'false');
            """
        )

        cur.execute(
            """
            INSERT OR REPLACE INTO encryption (key, value) VALUES (?, ?)
            """, ('key_hash', None)
        )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users
        (
            username            TEXT PRIMARY KEY,
            password            BLOB NOT NULL,
            api_key_hash        TEXT UNIQUE,
            email               TEXT,
            avatar              BLOB,
            avatarLastChanged   REAL,
            scrobblingEnabled   INTEGER DEFAULT 1,
            adminRole           INTEGER DEFAULT 0,
            settingsRole        INTEGER DEFAULT 1,
            streamRole          INTEGER DEFAULT 1,
            jukeboxRole         INTEGER DEFAULT 0,
            downloadRole        INTEGER DEFAULT 0,
            uploadRole          INTEGER DEFAULT 0,
            coverArtRole        INTEGER DEFAULT 0,
            playlistRole        INTEGER DEFAULT 1,
            commentRole         INTEGER DEFAULT 1,
            podcastRole         INTEGER DEFAULT 0,
            shareRole           INTEGER DEFAULT 0,
            videoConversionRole INTEGER DEFAULT 0,
            folder              INTEGER DEFAULT 0,
            maxBitRate          INTEGER DEFAULT 0  -- 0 = no limit, otherwise kbps: 32/40/48/56/64/80/96/112/128/160/192/224/256/320
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS likes
        (
            username   TEXT    NOT NULL,
            item_id    TEXT    NOT NULL, -- subsonic ID (can be anything, sg-1, al-2, ar-xxx, etc)
            starred_at REAL    NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (username, item_id),
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bookmarks
        (
            username TEXT    NOT NULL,
            song_id  INTEGER NOT NULL,
            position REAL    NOT NULL DEFAULT 0, -- playback offset (milliseconds)
            comment  TEXT,
            created  REAL    NOT NULL DEFAULT (unixepoch()),
            changed  REAL    NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (username, song_id),
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ratings
        (
            username  TEXT    NOT NULL,
            item_id   TEXT    NOT NULL, -- subsonic ID (can be anything, sg-1, al-2, ar-xxx, etc)
            rating    INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            rated_at  REAL    NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (username, item_id),
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS play_queue
        (
            username   TEXT PRIMARY KEY,
            current    INTEGER,        -- song_id currently queued up
            position   REAL DEFAULT 0, -- offset in the song (ms)
            changed    REAL,           -- last save timestamp
            changed_by TEXT,           -- Subsonic client name that saved the queue
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS play_queue_entries
        (
            username TEXT    NOT NULL,
            position INTEGER NOT NULL,
            song_id  INTEGER NOT NULL,
            PRIMARY KEY (username, position),
            FOREIGN KEY (username) REFERENCES play_queue (username)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS play_stats
        (
            username    TEXT    NOT NULL,
            song_id     INTEGER NOT NULL,
            play_count  INTEGER NOT NULL DEFAULT 0,
            last_played REAL, -- timestamp of most recent play
            PRIMARY KEY (username, song_id),
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS now_playing
        (
            username    TEXT PRIMARY KEY,
            song_id     INTEGER NOT NULL,
            started_at  REAL    NOT NULL,
            player_name TEXT    NOT NULL DEFAULT '',
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
        )
        """
    )
    # ephemeral: clears on startup
    cur.execute("DELETE FROM now_playing")

    # Indices for per-user queries (most common accesses)
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_likes_username       ON likes(username);""")
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_play_stats_username  ON play_stats(username);""")
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_bookmarks_username   ON bookmarks(username);""")
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_ratings_username     ON ratings(username);""")
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_play_queue_username  ON play_queue_entries(username);""")

    # These are or JOIN queries in albums (starred, frequent, highest sort)
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_likes_item_id        ON likes(item_id);""")
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_ratings_item_id      ON ratings(item_id);""")
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_play_stats_song_id   ON play_stats(song_id);""")

    conn.commit()
    conn.close()

    # TODO: Could support key rotation and eventually encryption of a clear db? But that's probably not worth the hassle
    if cipher is not None and not verify_key():
        raise RuntimeError(
            "BEETSTREAMNEXT_KEY has changed since the database was initialised. Stored passwords are unrecoverable with the current key. "
            f"\nRestore the original key, or delete the database (`{flask.current_app.config['DB_PATH']}`) and run initial setup again."
        )

    # TODO: should add db migration for future db changes


##

def database():
    """Get internal database connection."""
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DB_PATH'])
        g.db.execute("PRAGMA foreign_keys = ON;")
        g.db.execute("PRAGMA journal_mode = WAL;")
        g.db.execute("PRAGMA synchronous = NORMAL;")
        g.db.row_factory = sqlite3.Row
    return g.db


def dual_database():
    """Get internal database with the Beets library attached."""
    db = database()
    if not getattr(g, 'beets_attached', False):
        beets_path = Path(os.fsdecode(current_app.config['BEETS_DB_PATH']))
        if not beets_path.is_file():
            raise RuntimeError(f"Beets database not found at '{beets_path}'")

        db.execute("ATTACH DATABASE ? AS beets", (str(beets_path),))
        g.beets_attached = True
    return db


def close_database(e=None):
    """Closes the database at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()