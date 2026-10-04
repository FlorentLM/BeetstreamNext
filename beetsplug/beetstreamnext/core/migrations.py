from __future__ import annotations
import sqlite3
from pathlib import Path
import beets
import flask

from beetsplug.beetstreamnext.core.logging import bsn_logger


def apply_db_migrations(cursor: sqlite3.Cursor) -> None:

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
        have_table = cursor.execute(
            """SELECT 1 FROM sqlite_master WHERE type='table' AND name='likes'"""
        ).fetchone()

        if not have_table:
            # fresh install, nothing to migrate
            curr_version = MIGRATION_5_VER
        else:
            beets_db_path = flask.current_app.config.get('BEETS_DB_PATH')
            if beets_db_path and Path(beets_db_path).is_file():
                _migrate_to_stable_album_ids(cursor.connection, beets_db_path)
                curr_version = MIGRATION_5_VER
            else:
                bsn_logger.warning('Beets database not found... Stable album id migration deferred to next startup.')

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
    from beetsplug.beetstreamnext.core.ids import IDs

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
    from beetsplug.beetstreamnext.core.ids import IDs

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
