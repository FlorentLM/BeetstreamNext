from typing import Any, Optional
import binascii
import json
import secrets
import sqlite3
import os
import base64
import hashlib
import time
import beets
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from pathlib import Path
from functools import lru_cache

import flask

from beetsplug.beetstreamnext.console import print_box, TermColors
from beetsplug.beetstreamnext.constants import ALPHANUM_CHARS, SESSION_KEY_ROTATION_DAYS
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.schemas import USER_ROLES_SCHEMA
from beetsplug.beetstreamnext.utils.db import get_beets_schema


##

def _write_secret_file(path: Path, content: str) -> None:
    """Write a secret to disk, 0o600 from creation."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
    except Exception:
        os.close(fd)   # only for if fdopen failed
        raise

# Secrets management

def rotate_session_key(cache_dir: str | Path) -> str:
    """
    Loads the admin session signing key from the cache directory, rotating it
    if it is older than _SESSION_KEY_ROTATION_DAYS.
    """

    cache_dir = Path(cache_dir)
    key_file = cache_dir / '.beetstreamnext_session'

    if key_file.exists():
        try:
            key_file.chmod(0o600)
            data = json.loads(key_file.read_text())
            age_days = (time.time() - data['generated_at']) / 86400
            if age_days < SESSION_KEY_ROTATION_DAYS:
                return data['key']
        except (json.JSONDecodeError, KeyError, OSError):
            pass   # malformed file, regenerate

    new_key = secrets.token_urlsafe(32)
    cache_dir.mkdir(parents=True, exist_ok=True)
    _write_secret_file(key_file, json.dumps({'key': new_key, 'generated_at': time.time()}))
    return new_key


def ensure_secret(db_path: str | Path) -> None:
    """
    Called once at startup, before initialise_db().
    Generates the BEETSTREAMNEXT_KEY, saves it to .env, displays it. Once.
    """

    db_path = Path(db_path)
    env_path = db_path.parent / '.env'

    # Load whatever is already in the env before deciding
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
    else:
        load_dotenv(override=False)

    is_first_run = not db_path.exists()

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

        _write_secret_file(env_path, '\n'.join(new_lines) + '\n')

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
    conn = sqlite3.connect(flask.current_app.config['BSN_DB_PATH'])
    cur = conn.cursor()

    cur.execute("PRAGMA busy_timeout = 5000;")
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")
    cur.execute("PRAGMA foreign_keys = ON;")

    # Metadata table for version tracking
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS db_metadata (key TEXT PRIMARY KEY, value TEXT)
        """
    )

    _apply_db_migrations(cur)

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
                    'BEETSTREAMNEXT_KEY has changed since the database was initialised. '
                    'Stored passwords are unrecoverable with the current key.\n'
                    f'Restore the original key, or delete the database '
                    f"(`{flask.current_app.config['BSN_DB_PATH']}`) and run initial setup again."
                )

        elif stored_hash is not None:
            # Cipher gone but db has encrypted passwords: no good
            conn.close()
            raise RuntimeError(
                'Database contains encrypted passwords but BEETSTREAMNEXT_KEY is not set. '
                'Passwords cannot be decrypted.'
            )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings
        (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            encrypted  INTEGER NOT NULL DEFAULT 0,
            updated_at REAL    NOT NULL DEFAULT (unixepoch())
        )
        """
    )

    role_columns_sql = ",\n            ".join([
        f'{name} INTEGER DEFAULT {1 if default else 0}'
        for name, _, default in USER_ROLES_SCHEMA
    ])

    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS users
        (
            username            TEXT PRIMARY KEY,
            password            BLOB NOT NULL,
            api_key_hash        TEXT UNIQUE,
            email               TEXT,
            avatar              BLOB,
            avatarLastChanged   REAL,
            folder              INTEGER DEFAULT 0,
            maxBitRate          INTEGER DEFAULT 0,  -- 0 = no limit, otherwise kbps: 32/40/48/56/64/80/96/112/128/160/192/224/256/320
            {role_columns_sql}
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
            username TEXT NOT NULL,
            song_id  TEXT NOT NULL, -- subsonic song or podcast episode ID (sg-m-xxx, sg-h-xxx, legacy sg-<row id>, or pe-<row id>)
            position REAL NOT NULL DEFAULT 0, -- playback offset (milliseconds)
            comment  TEXT,
            created  REAL NOT NULL DEFAULT (unixepoch()),
            changed  REAL NOT NULL DEFAULT (unixepoch()),
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
        CREATE TABLE IF NOT EXISTS internet_radio_stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            stream_url TEXT NOT NULL,
            homepage_url TEXT,
            image BLOB,
            image_mtime REAL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS podcast_channels
        (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            url           TEXT NOT NULL UNIQUE,
            title         TEXT,
            description   TEXT,
            image         BLOB,
            image_url     TEXT,
            status        TEXT NOT NULL DEFAULT 'new', -- new/downloading/completed/error/deleted/skipped (PodcastStatus)
            error_message TEXT,
            created       REAL NOT NULL DEFAULT (unixepoch())
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS podcast_episodes
        (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id    INTEGER NOT NULL,
            guid          TEXT NOT NULL, -- feed <guid> (or link/title fallback), unique per channel
            title         TEXT,
            description   TEXT,
            publish_date  REAL,
            audio_url     TEXT,
            duration      REAL,   -- seconds
            file_path     TEXT,   -- absolute local path once downloaded
            file_size     INTEGER,
            status        TEXT NOT NULL DEFAULT 'new', -- new/downloading/completed/error/deleted/skipped (PodcastStatus)
            error_message TEXT,
            created       REAL NOT NULL DEFAULT (unixepoch()),
            UNIQUE (channel_id, guid),
            FOREIGN KEY (channel_id) REFERENCES podcast_channels (id) ON DELETE CASCADE
        )
        """
    )

    cur.execute("""CREATE INDEX IF NOT EXISTS idx_podcast_episodes_channel ON podcast_episodes(channel_id);""")
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_podcast_episodes_publish ON podcast_episodes(publish_date);""")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS podcast_subscriptions
        (
            username      TEXT NOT NULL,
            channel_id    INTEGER NOT NULL,
            subscribed_at REAL NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (username, channel_id),
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE,
            FOREIGN KEY (channel_id) REFERENCES podcast_channels (id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        -- one row = username wants episode_id kept on disk
        CREATE TABLE IF NOT EXISTS podcast_episode_downloads
        (
            username     TEXT NOT NULL,
            episode_id   INTEGER NOT NULL,
            requested_at REAL NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (username, episode_id),
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE,
            FOREIGN KEY (episode_id) REFERENCES podcast_episodes (id) ON DELETE CASCADE
        )
        """
    )

    cur.execute("""CREATE INDEX IF NOT EXISTS idx_podcast_subscriptions_channel ON podcast_subscriptions(channel_id);""")
    cur.execute("""CREATE INDEX IF NOT EXISTS idx_podcast_episode_downloads_episode ON podcast_episode_downloads(episode_id);""")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS shares
        (
            id          TEXT PRIMARY KEY,
            username    TEXT NOT NULL,
            description TEXT,
            expires     REAL,
            created     REAL NOT NULL DEFAULT (unixepoch()),
            visit_count INTEGER       DEFAULT 0,
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS share_entries
        (
            share_id TEXT NOT NULL,
            item_id  TEXT NOT NULL, -- sg-xxx or al-xxx
            FOREIGN KEY (share_id) REFERENCES shares (id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS play_queue
        (
            username   TEXT PRIMARY KEY,
            current    TEXT,           -- subsonic song ID currently queued up
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
            song_id  TEXT    NOT NULL, -- subsonic song ID
            PRIMARY KEY (username, position),
            FOREIGN KEY (username) REFERENCES play_queue (username) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS play_stats
        (
            username    TEXT NOT NULL,
            song_id     TEXT NOT NULL, -- subsonic song ID
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
            username      TEXT PRIMARY KEY,
            item_id       TEXT NOT NULL,
            started_at    REAL NOT NULL,
            player_name   TEXT NOT NULL DEFAULT '',
            position_ms   INTEGER DEFAULT 0,
            state         TEXT DEFAULT 'stopped',
            playback_rate REAL DEFAULT 1.0,
            scrobbled     INTEGER DEFAULT 0,  -- flag to prevent double scrobbles
            FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_messages
        (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,   -- voluntarily not Foreign-Keyed to users
            time     REAL NOT NULL,   -- timestamp in ms
            message  TEXT NOT NULL
        )
        """
    )

    cur.execute("""CREATE INDEX IF NOT EXISTS idx_chat_time ON chat_messages (time);""")

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


##

def _apply_db_migrations(cursor: sqlite3.Cursor) -> None:

    # Read current version stored in db
    row = cursor.execute(
        """
        SELECT value
        FROM db_metadata
        WHERE key = 'version'
        """
    ).fetchone()
    curr_version = int(row[0]) if row else 0

    # Apply migrations

    ## _________ Migration 1: Version 0 -> 1 (renamed song_id to item_id), 19/07/2026 14:40
    MIGRATION_1_VER = 1

    if curr_version < MIGRATION_1_VER:
        cursor.execute("""DROP TABLE IF EXISTS now_playing""")
        curr_version = MIGRATION_1_VER

    ## _________ Migration 2: Version 1 -> 2 (add ON DELETE CASCADE to play_queue_entries), 09/08/2026, 01:00
    MIGRATION_2_VER = 2

    if curr_version < MIGRATION_2_VER:
        have_table = cursor.execute(
            """SELECT 1 FROM sqlite_master WHERE type='table' AND name='play_queue_entries'"""
        ).fetchone()
        if have_table:
            _rebuild_play_queue_entries(cursor.connection)
        curr_version = MIGRATION_2_VER

    ## _________ Migration 3: Version 2 -> 3 (bookmarks/play_queue*/play_stats switch from the
    ##            raw beets row id to a stable subsonic song id, same as likes/ratings), 23/08/2026
    MIGRATION_3_VER = 3

    if curr_version < MIGRATION_3_VER:
        have_table = cursor.execute(
            """SELECT 1 FROM sqlite_master WHERE type='table' AND name='bookmarks'"""
        ).fetchone()

        if not have_table:
            # Fresh install: bookmarks/likes/ratings/play_queue*/play_stats are all created further
            # below, already in their current (post-migration) shape - nothing to migrate.
            curr_version = MIGRATION_3_VER
        else:
            beets_db_path = flask.current_app.config.get('BEETS_DB_PATH')
            if beets_db_path and Path(beets_db_path).is_file():
                _migrate_to_stable_song_ids(cursor.connection, beets_db_path)
                curr_version = MIGRATION_3_VER
            else:
                bsn_logger.warning('Beets database not found - stable song id migration deferred to next startup.')

    ## _________ Migration 4: Version 3 -> 4 (drop the Foreign Key on chat_messages.username
    MIGRATION_4_VER = 4

    chat_messages_still_fkd = cursor.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'chat_messages' AND sql LIKE '%FOREIGN KEY%'
        """
    ).fetchone()

    if chat_messages_still_fkd:
        _rebuild_chat_messages(cursor.connection)

    if curr_version < MIGRATION_4_VER:
        curr_version = MIGRATION_4_VER

    ## _________ Migration 5: Version 4 -> 5 (likes/ratings/share_entries switch album ids
    ##            from the raw beets row id to a stable subsonic album id, same as song ids), 30/08/2026
    MIGRATION_5_VER = 5

    if curr_version < MIGRATION_5_VER:
        beets_db_path = flask.current_app.config.get('BEETS_DB_PATH')
        if beets_db_path and Path(beets_db_path).is_file():
            _migrate_to_stable_album_ids(cursor.connection, beets_db_path)
            curr_version = MIGRATION_5_VER
        else:
            bsn_logger.warning('Beets database not found - stable album id migration deferred to next startup.')

    ## ___________________________________________________________________

    # Update version in db
    cursor.execute(
        """
        INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('version', ?)
        """, (curr_version,)
    )

def _rebuild_play_queue_entries(conn: sqlite3.Connection) -> None:
    """
    Recreate play_queue_entries with ON DELETE CASCADE on its FK to play_queue.
    FK enforcement must be off during the swap and toggling it can't
    happen inside a transaction so commit/BEGIN is needed
    """
    conn.commit()   # close any implicit transaction before toggling FK enforcement
    conn.execute("""PRAGMA foreign_keys = OFF""")
    try:
        conn.execute("""BEGIN""")
        conn.execute(
            """
            CREATE TABLE play_queue_entries_new
            (
                username TEXT    NOT NULL,
                position INTEGER NOT NULL,
                song_id  INTEGER NOT NULL,
                PRIMARY KEY (username, position),
                FOREIGN KEY (username) REFERENCES play_queue (username) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            INSERT INTO play_queue_entries_new (username, position, song_id)
            SELECT username, position, song_id FROM play_queue_entries
            """
        )
        conn.execute("""DROP TABLE play_queue_entries""")
        conn.execute("""ALTER TABLE play_queue_entries_new RENAME TO play_queue_entries""")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("""PRAGMA foreign_keys = ON""")

def _rebuild_chat_messages(conn: sqlite3.Connection) -> None:
    """
    Recreate chat_messages without the foreign key to users(username)
    """
    conn.commit()   # close any implicit transaction before toggling FK enforcement
    conn.execute("""PRAGMA foreign_keys = OFF""")
    try:
        conn.execute("""BEGIN""")
        conn.execute(
            """
            CREATE TABLE chat_messages_new
            (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                time     REAL NOT NULL,
                message  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO chat_messages_new (id, username, time, message)
            SELECT id, username, time, message FROM chat_messages
            """
        )
        conn.execute("""DROP TABLE chat_messages""")
        conn.execute("""ALTER TABLE chat_messages_new RENAME TO chat_messages""")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("""PRAGMA foreign_keys = ON""")

def _migrate_to_stable_song_ids(conn: sqlite3.Connection, beets_db_path) -> None:
    """
    bookmarks/play_queue/play_queue_entries/play_stats were keyed on the raw beets row id,
    which a delete+reimport broke.

    They switch here to the same stable subsonic song id likes/ratings already use
    (an mbid-derived id, or a path hash for songs with none).

    Existing rows are re-keyed via a lookup built from the current beets library,
    matched by the row id they were saved under before this migration.

    Rows for a song that no longer exists in the library are dropped.
    """
    from beetsplug.beetstreamnext.core.mappings import IDs

    # this migration runs before app.config['root_directory'] is set so this is needed
    _root_dir = beets.config['directory'].get()

    conn.commit()   # close any implicit transaction before toggling FK enforcement
    conn.execute("""PRAGMA foreign_keys = OFF""")
    try:
        conn.execute("""ATTACH DATABASE ? AS beets_lib""", (str(beets_db_path),))

        item_rows = conn.execute(
            """
            SELECT id, mb_trackid, mb_releasetrackid, path 
            FROM beets_lib.items
            """
        ).fetchall()

        id_map = {
            row[0]: IDs.encode_song(
                {'id': row[0], 'mb_trackid': row[1], 'mb_releasetrackid': row[2], 'path': row[3]},
                _root_directory=_root_dir
            )
            for row in item_rows
        }

        conn.execute("""BEGIN""")

        # bookmarks: song_id INTEGER -> TEXT
        conn.execute(
            """
            CREATE TABLE bookmarks_new
            (
                username TEXT NOT NULL,
                song_id  TEXT NOT NULL,
                position REAL NOT NULL DEFAULT 0,
                comment  TEXT,
                created  REAL NOT NULL DEFAULT (unixepoch()),
                changed  REAL NOT NULL DEFAULT (unixepoch()),
                PRIMARY KEY (username, song_id),
                FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
            )
            """
        )
        for old_id, username, position, comment, created, changed in conn.execute(
            """SELECT song_id, username, position, comment, created, changed FROM bookmarks"""
        ).fetchall():
            new_id = id_map.get(old_id)
            if new_id is None:
                continue    # song no longer exists in the library, drop orphaned bookmark
            conn.execute(
                """
                INSERT INTO bookmarks_new (username, song_id, position, comment, created, changed)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (username, song_id) DO NOTHING
                """, (username, new_id, position, comment, created, changed)
            )
        conn.execute("""DROP TABLE bookmarks""")
        conn.execute("""ALTER TABLE bookmarks_new RENAME TO bookmarks""")

        # play_queue: current INTEGER -> TEXT
        conn.execute(
            """
            CREATE TABLE play_queue_new
            (
                username   TEXT PRIMARY KEY,
                current    TEXT,
                position   REAL DEFAULT 0,
                changed    REAL,
                changed_by TEXT,
                FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
            )
            """
        )
        for username, current, position, changed, changed_by in conn.execute(
            """SELECT username, current, position, changed, changed_by FROM play_queue"""
        ).fetchall():
            new_current = id_map.get(current) if current is not None else None
            conn.execute(
                """
                INSERT INTO play_queue_new (username, current, position, changed, changed_by)
                VALUES (?, ?, ?, ?, ?)
                """, (username, new_current, position, changed, changed_by)
            )
        conn.execute("""DROP TABLE play_queue""")
        conn.execute("""ALTER TABLE play_queue_new RENAME TO play_queue""")

        # play_queue_entries: song_id INTEGER -> TEXT
        conn.execute(
            """
            CREATE TABLE play_queue_entries_new
            (
                username TEXT    NOT NULL,
                position INTEGER NOT NULL,
                song_id  TEXT    NOT NULL,
                PRIMARY KEY (username, position),
                FOREIGN KEY (username) REFERENCES play_queue (username) ON DELETE CASCADE
            )
            """
        )
        for username, position, song_id in conn.execute(
            """SELECT username, position, song_id FROM play_queue_entries"""
        ).fetchall():
            new_id = id_map.get(song_id)
            if new_id is None:
                continue
            conn.execute(
                """
                INSERT INTO play_queue_entries_new (username, position, song_id)
                VALUES (?, ?, ?)
                """, (username, position, new_id)
            )
        conn.execute("""DROP TABLE play_queue_entries""")
        conn.execute("""ALTER TABLE play_queue_entries_new RENAME TO play_queue_entries""")

        # play_stats: song_id INTEGER -> TEXT (merge counts if two old rows collide)
        conn.execute(
            """
            CREATE TABLE play_stats_new
            (
                username    TEXT NOT NULL,
                song_id     TEXT NOT NULL,
                play_count  INTEGER NOT NULL DEFAULT 0,
                last_played REAL,
                PRIMARY KEY (username, song_id),
                FOREIGN KEY (username) REFERENCES users (username) ON DELETE CASCADE
            )
            """
        )
        for username, song_id, play_count, last_played in conn.execute(
            """SELECT username, song_id, play_count, last_played FROM play_stats"""
        ).fetchall():
            new_id = id_map.get(song_id)
            if new_id is None:
                continue
            conn.execute(
                """
                INSERT INTO play_stats_new (username, song_id, play_count, last_played)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (username, song_id) DO UPDATE SET
                    play_count  = play_count + excluded.play_count,
                    last_played = MAX(last_played, excluded.last_played)
                """, (username, new_id, play_count, last_played)
            )
        conn.execute("""DROP TABLE play_stats""")
        conn.execute("""ALTER TABLE play_stats_new RENAME TO play_stats""")

        # likes/ratings: item_id is already TEXT, but may hold the old 'sg-<row id>' form
        for table, ts_col in (('likes', 'starred_at'), ('ratings', 'rated_at')):
            legacy_rows = conn.execute(
                f"""SELECT rowid, item_id FROM {table} WHERE item_id GLOB 'sg-[0-9]*'"""
            ).fetchall()
            for rowid, item_id in legacy_rows:
                try:
                    old_beets_id = int(item_id[len('sg-'):])
                except ValueError:
                    continue
                new_id = id_map.get(old_beets_id)
                if new_id is None or new_id == item_id:
                    continue
                try:
                    conn.execute(f"""UPDATE {table} SET item_id = ? WHERE rowid = ?""", (new_id, rowid))
                except sqlite3.IntegrityError:
                    # a row for (username, new_id) already exists: drop the stale duplicate
                    conn.execute(f"""DELETE FROM {table} WHERE rowid = ?""", (rowid,))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            conn.execute("""DETACH DATABASE beets_lib""")
        except sqlite3.Error:
            pass
        conn.execute("""PRAGMA foreign_keys = ON""")

def _migrate_to_stable_album_ids(conn: sqlite3.Connection, beets_db_path) -> None:
    """
    likes/ratings/share_entries could hold album ids keyed on the raw beets row id
    ('al-<id>'), which a delete+reimport could repoint at a different album.

    Existing rows are re-keyed via a lookup on the current beets library, matched
    by the row id they were saved under before this migration.

    A row for an album that no longer exists in the library is left untouched,
    there's nothing to remap it to, and it's harmless anyway.
    """
    from beetsplug.beetstreamnext.core.mappings import IDs

    conn.commit()   # close any implicit transaction before toggling FK enforcement
    conn.execute("""PRAGMA foreign_keys = OFF""")
    try:
        conn.execute("""ATTACH DATABASE ? AS beets_lib""", (str(beets_db_path),))

        album_rows = conn.execute(
            """
            SELECT id, mb_albumid, albumartist, album
            FROM beets_lib.albums
            """
        ).fetchall()

        id_map = {
            row[0]: IDs.encode_album(row[0], row[1], row[2], row[3])
            for row in album_rows
        }

        conn.execute("""BEGIN""")

        for table in ('likes', 'ratings'):
            legacy_rows = conn.execute(
                f"""SELECT rowid, item_id FROM {table} WHERE item_id GLOB 'al-[0-9]*'"""
            ).fetchall()
            for rowid, item_id in legacy_rows:
                try:
                    old_beets_id = int(item_id[len('al-'):])
                except ValueError:
                    continue
                new_id = id_map.get(old_beets_id)
                if new_id is None or new_id == item_id:
                    continue
                try:
                    conn.execute(f"""UPDATE {table} SET item_id = ? WHERE rowid = ?""", (new_id, rowid))
                except sqlite3.IntegrityError:
                    # a row for (username, new_id) already exists: drop the stale duplicate
                    conn.execute(f"""DELETE FROM {table} WHERE rowid = ?""", (rowid,))

        legacy_shares = conn.execute(
            """SELECT rowid, item_id FROM share_entries WHERE item_id GLOB 'al-[0-9]*'"""
        ).fetchall()
        for rowid, item_id in legacy_shares:
            try:
                old_beets_id = int(item_id[len('al-'):])
            except ValueError:
                continue
            new_id = id_map.get(old_beets_id)
            if new_id is None or new_id == item_id:
                continue
            conn.execute("""UPDATE share_entries SET item_id = ? WHERE rowid = ?""", (new_id, rowid))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            conn.execute("""DETACH DATABASE beets_lib""")
        except sqlite3.Error:
            pass
        conn.execute("""PRAGMA foreign_keys = ON""")

##

def database() -> sqlite3.Connection:
    """Get internal database connection."""
    if 'db' not in flask.g:
        flask.g.db = sqlite3.connect(flask.current_app.config['BSN_DB_PATH'])
        flask.g.db.execute("""PRAGMA main.journal_mode = WAL;""")
        flask.g.db.execute("""PRAGMA synchronous = NORMAL;""")
        flask.g.db.execute("""PRAGMA busy_timeout = 5000;""")
        flask.g.db.execute("""PRAGMA foreign_keys = ON;""")
        flask.g.db.row_factory = sqlite3.Row
    return flask.g.db


def dual_database() -> sqlite3.Connection:
    """Get internal database with the Beets library attached."""
    db = database()
    if not getattr(flask.g, 'beets_attached', False):
        beets_path = Path(os.fsdecode(flask.current_app.config['BEETS_DB_PATH']))
        if not beets_path.is_file():
            raise RuntimeError(f"Beets database not found at '{beets_path}'")

        db.execute("""ATTACH DATABASE ? AS beets""", (str(beets_path),))
        flask.g.beets_attached = True
    return db


def close_database(_e: Optional[Any] = None) -> None:
    """Closes the database at the end of the request."""
    db = flask.g.pop('db', None)
    if db is not None:
        db.close()


##

def write_beets_field(
    entity_type: str,
    entity_id: int,
    key: str,
    value: Any,
    allow_flex: bool = False,
) -> None:
    """
    Writes a field in the beets database.
    """

    if entity_type not in ('item', 'album'):
        raise ValueError("entity_type must be 'item' or 'album'")

    if not isinstance(key, str) or not ALPHANUM_CHARS.match(key):
        raise ValueError(f'Invalid field name: {key!r}')

    entity_id = int(entity_id)

    core_table = 'items' if entity_type == 'item' else 'albums'
    attr_table = f'{entity_type}_attributes'

    db = dual_database()

    if key in get_beets_schema(core_table):
        cur = db.execute(
            f"""
            UPDATE beets.{core_table} 
            SET {key} = ? 
            WHERE id = ?
            """, (value, entity_id),
        )
        db.commit()

        # If that worked but changed 0 rows (wrong ID), user should know
        if cur.rowcount == 0:
            bsn_logger.warning(f'No beets {entity_type} found with ID {entity_id}')
        return

    if not allow_flex:
        raise ValueError(
            f"'{key}' is not a column of beets.{core_table}. "
            f"Pass allow_flex=True to write it as a flexible attribute."
        )

    db.execute(
        f"""
        INSERT INTO beets.{attr_table} (entity_id, key, value)
        VALUES (?, ?, ?)
        ON CONFLICT(entity_id, key) DO UPDATE SET value = excluded.value
        """,
        (entity_id, key, str(value)),
    )
    db.commit()