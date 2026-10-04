from __future__ import annotations
import sqlite3
import flask

from beetsplug.beetstreamnext.constants import DB_BUSY_TIMEOUT_MS
from beetsplug.beetstreamnext.core.encryption import get_cipher, get_key_hash
from beetsplug.beetstreamnext.core.migrations import apply_db_migrations
from beetsplug.beetstreamnext.schemas import USER_ROLES_SCHEMA


def initialise_db() -> None:
    conn = sqlite3.connect(flask.current_app.config['BSN_DB_PATH'])
    cur = conn.cursor()

    cur.execute(f"PRAGMA busy_timeout = {int(DB_BUSY_TIMEOUT_MS)};")
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")
    cur.execute("PRAGMA foreign_keys = ON;")

    # Metadata table for version tracking
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS db_metadata (key TEXT PRIMARY KEY, value TEXT)
        """
    )

    apply_db_migrations(cur)

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
        CREATE TABLE IF NOT EXISTS pinned_import_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            incremental INTEGER NOT NULL DEFAULT 1,
            watch INTEGER NOT NULL DEFAULT 0,
            last_triggered REAL
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
        -- one row per (song, check kind): a background health scan's findings for a song.
        CREATE TABLE IF NOT EXISTS song_checks
        (
            song_id    TEXT    NOT NULL, -- subsonic song id
            kind       TEXT    NOT NULL, -- for example 'decode_errors'
            mtime      REAL    NOT NULL, -- source file's mtime as of last check
            ok         INTEGER NOT NULL, -- 1 = passed, 0 = flagged
            detail     TEXT,             -- short note
            checked_at REAL    NOT NULL DEFAULT (unixepoch()),
            PRIMARY KEY (song_id, kind)
        )
        """
    )

    cur.execute("""CREATE INDEX IF NOT EXISTS idx_song_checks_kind ON song_checks(kind);""")

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
