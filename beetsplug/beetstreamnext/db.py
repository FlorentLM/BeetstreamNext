import binascii
import json
import secrets
import sqlite3
import os
import base64
import hashlib
import time
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from pathlib import Path
from functools import lru_cache
from typing import Optional

import flask
from flask import g, current_app


_SESSION_KEY_ROTATION_DAYS = 30


##
# Secrets management

def rotate_session_key(cache_dir: Path) -> str:
    """
    Loads the admin session signing key from the cache directory, rotating it
    if it is older than _SESSION_KEY_ROTATION_DAYS.
    """
    key_file = cache_dir / '.beetstreamnext_session'

    if key_file.exists():
        try:
            data = json.loads(key_file.read_text())
            age_days = (time.time() - data['generated_at']) / 86400
            if age_days < _SESSION_KEY_ROTATION_DAYS:
                return data['key']
        except (json.JSONDecodeError, KeyError, OSError):
            pass   # malformed file, regenerate

    new_key = secrets.token_urlsafe(32)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key_file.write_text(json.dumps({'key': new_key, 'generated_at': time.time()}))
    key_file.chmod(0o600)
    return new_key


def ensure_secret(db_path: Path) -> None:
    """
    Called once at startup, before initialise_db().
    Generates the BEETSTREAMNEXT_KEY, saves it to .env, displays it. Once.
    """
    env_path = db_path.parent / '.env'

    # Load whatever is already in the env before deciding
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
    else:
        load_dotenv(override=False)

    is_first_run = not db_path.exists()

    from beetsplug.beetstreamnext import TermColors, print_box

    if is_first_run:
        existing_lines = env_path.read_text().splitlines() if env_path.exists() else []
        already_set = {line.split('=', 1)[0] for line in existing_lines if '=' in line}

        new_lines = list(existing_lines)

        # Generate and record key
        if 'BEETSTREAMNEXT_KEY' not in already_set:
            enc_key = Fernet.generate_key().decode()
            new_lines.append(f'BEETSTREAMNEXT_KEY={enc_key}')
            os.environ['BEETSTREAMNEXT_KEY'] = enc_key
        else:
            enc_key = os.environ['BEETSTREAMNEXT_KEY']   # was loaded by load_dotenv above

        env_path.write_text('\n'.join(new_lines) + '\n')
        env_path.chmod(0o600)

        print_box([
            '',
            f'{TermColors.WARNING + TermColors.BOLD + TermColors.REVERSE}  BEETSTREAMNEXT: First run setup  {TermColors.ENDC}',
            '',
            'An encryption key has been generated for your database:',
            '',
            f'{TermColors.BOLD}BEETSTREAMNEXT_KEY={enc_key}{TermColors.ENDC}',
            '',
            'It has been saved to:',
            f'{env_path}',
            '',
            "  ▶  It won't be shown again. Store it safely.",
            '  ▶  If you lose it, stored passwords will be unrecoverable.',
            '',
        ], color=TermColors.WARNING)

    else:
        # Not first run, key must be present
        if not os.environ.get('BEETSTREAMNEXT_KEY'):
            print_box([
                '',
                f'{TermColors.FAIL + TermColors.BOLD + TermColors.REVERSE}  STARTUP FAILED: Missing required secret  {TermColors.ENDC}',
                '',
                f'Add the {TermColors.BOLD}BEETSTREAMNEXT_KEY{TermColors.ENDC} to:',
                # '',
                f'{env_path}',
                '',
                'If you have lost the BEETSTREAMNEXT_KEY, stored passwords',
                'are unrecoverable. Delete the database and run setup again.',
                '',
            ], color=TermColors.FAIL)
            exit(1)


##

@lru_cache(maxsize=1)
def _cipher_for(key: str) -> Fernet | None:
    """Fernet for a given key string. Cached for the process lifetime."""
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        return None


@lru_cache(maxsize=1)
def _hash_for(key: str) -> str:
    """SHA256 of the decoded key bytes. Also cached."""
    return hashlib.sha256(base64.urlsafe_b64decode(key)).hexdigest()


def get_cipher() -> Fernet | None:
    key = os.environ.get('BEETSTREAMNEXT_KEY')
    if not key:
        return None
    return _cipher_for(key)


def get_key_hash() -> str | None:
    key = os.environ.get('BEETSTREAMNEXT_KEY')
    if not key:
        return None
    try:
        return _hash_for(key)
    except binascii.Error:
        return None


def verify_key() -> bool:

    with database() as db:
        result = db.execute("""SELECT value FROM encryption WHERE key = 'key_hash'""").fetchone()

    stored_hash = result[0] if result else None
    current_hash = get_key_hash()

    return current_hash == stored_hash


def initialise_db() -> None:
    conn = sqlite3.connect(flask.current_app.config['DB_PATH'])
    cur = conn.cursor()

    cur.execute("PRAGMA busy_timeout = 5000;")
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")
    cur.execute("PRAGMA foreign_keys = ON;")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS encryption (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    cipher = get_cipher()
    existing = cur.execute(
        """
        SELECT value FROM encryption WHERE key = 'key_hash'
        """
    ).fetchone()

    if existing is None:
        # First run: record current key hash (if encryption is configured)
        if cipher is not None:
            cur.execute(
                """
                INSERT INTO encryption (key, value) VALUES ('key_hash', ?)
                """, (get_key_hash(),),
            )
    else:
        stored_hash = existing[0]   # could be NULL from a pre-encryption install

        if cipher is not None:
            if stored_hash is None:
                # Upgrading a clear DB to encrypted: record new hash
                cur.execute(
                    """
                    UPDATE encryption SET value = ? WHERE key = 'key_hash'
                    """, (get_key_hash(),),
                )

            elif stored_hash != get_key_hash():
                conn.close()
                raise RuntimeError(
                    "BEETSTREAMNEXT_KEY has changed since the database was initialised. "
                    "Stored passwords are unrecoverable with the current key.\n"
                    f"Restore the original key, or delete the database "
                    f"(`{flask.current_app.config['DB_PATH']}`) and run initial setup again."
                )

        elif stored_hash is not None:
            # Cipher gone but db has encrypted passwords: no good
            conn.close()
            raise RuntimeError(
                "Database contains encrypted passwords but BEETSTREAMNEXT_KEY is not set. "
                "Passwords cannot be decrypted."
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
    cur.execute("""DELETE FROM now_playing""")

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

    # TODO: should add db migration for future db changes


##

def database() -> sqlite3.Connection:
    """Get internal database connection."""
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DB_PATH'])
        g.db.execute("PRAGMA main.journal_mode = WAL;")
        g.db.execute("PRAGMA synchronous = NORMAL;")
        g.db.execute("PRAGMA busy_timeout = 5000;")
        g.db.execute("PRAGMA foreign_keys = ON;")
        g.db.row_factory = sqlite3.Row
    return g.db


def dual_database() -> sqlite3.Connection:
    """Get internal database with the Beets library attached."""
    db = database()
    if not getattr(g, 'beets_attached', False):
        beets_path = Path(os.fsdecode(current_app.config['BEETS_DB_PATH']))
        if not beets_path.is_file():
            raise RuntimeError(f"Beets database not found at '{beets_path}'")

        db.execute("ATTACH DATABASE ? AS beets", (str(beets_path),))
        g.beets_attached = True
    return db


def close_database(e=None) -> None:
    """Closes the database at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()