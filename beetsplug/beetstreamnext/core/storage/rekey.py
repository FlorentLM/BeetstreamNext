from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Callable

from cryptography.fernet import InvalidToken

from beetsplug.beetstreamnext.constants import DB_BUSY_TIMEOUT_MS
from beetsplug.beetstreamnext.core.storage.encryption import InvalidKeyError, get_cipher, get_key_hash
from beetsplug.beetstreamnext.core.storage.schema import ENCRYPTED_COLUMNS


class RekeyError(RuntimeError):
    """The database could not be re-encrypted."""


def _reencrypt(conn: sqlite3.Connection, table: str, column: str, flag: str | None,
               convert: Callable[[bytes], bytes]) -> int:

    where = f"{flag} = 1" if flag else f"{column} IS NOT NULL"
    rows = conn.execute(
        f"""SELECT rowid, {column} 
        FROM {table} 
        WHERE {where}
        """).fetchall()    # nosec B608

    for rowid, stored in rows:
        token = stored if isinstance(stored, bytes) else stored.encode('utf-8')

        try:
            new = convert(token)
        except InvalidToken as e:
            raise RekeyError(f"Can't decrypt {table}.{column} (row {rowid}) with the old key.") from e

        conn.execute(
            f"""
            UPDATE {table} 
            SET {column} = ? WHERE rowid = ?
            """,     # nosec B608
            (new if isinstance(stored, bytes) else new.decode('utf-8'), rowid)
        )

    return len(rows)


def rekey_database(db_path: str | Path, old_key: str, new_key: str) -> tuple[int, Path]:
    """
    Re-encrypt everything in the database and record the new key hash. Server must be stopped.
    """

    db_path = Path(db_path)
    if not db_path.is_file():
        raise RekeyError(f"BeetstreamNext database not found at '{db_path}'.")

    try:
        old, new = get_cipher(old_key), get_cipher(new_key)
    except InvalidKeyError as e:
        raise RekeyError(f'Invalid old or new key: {e}') from e

    old_hash, new_hash = get_key_hash(old_key), get_key_hash(new_key)

    if old_hash == new_hash:
        raise RekeyError('The old and new keys are the same.')

    def convert(token: bytes) -> bytes:
        return new.encrypt(old.decrypt(token))

    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(DB_BUSY_TIMEOUT_MS)}")

        try:
            row = conn.execute(
                """
                SELECT value 
                FROM encryption 
                WHERE key = 'key_hash'
                """
            ).fetchone()

        except sqlite3.DatabaseError as e:
            raise RekeyError(f'Not a BeetstreamNext database: {e}') from e

        stored_hash = row[0] if row else None

        if stored_hash == new_hash:
            raise RekeyError('This database already uses the new key.')

        if stored_hash != old_hash:
            raise RekeyError('The old key does not match the one this database was created with.')

        previous = db_path.with_name(f"{db_path.name}.pre-rekey-{time.strftime('%Y%m%d-%H%M%S')}")
        previous.touch(mode=0o600)

        snapshot = sqlite3.connect(previous)
        try:
            conn.backup(snapshot)
        finally:
            snapshot.close()

        conn.execute("BEGIN IMMEDIATE")
        try:
            count = sum(_reencrypt(conn, *spec, convert) for spec in ENCRYPTED_COLUMNS)
            conn.execute(
                """
                UPDATE encryption 
                SET value = ? 
                WHERE key = 'key_hash'
                """, (new_hash,)
            )
            conn.execute("COMMIT")

        except sqlite3.OperationalError as e:
            conn.execute("ROLLBACK")
            raise RekeyError(f'Unexpected database layout ({e}): start BeetstreamNext once to upgrade it, then retry.') from e

        except BaseException:
            conn.execute("ROLLBACK")
            raise

    finally:
        conn.close()

    return count, previous
