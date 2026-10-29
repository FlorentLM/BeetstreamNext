from __future__ import annotations

import json
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from beetsplug.beetstreamnext.constants import DB_FILE_NAME, DB_BUSY_TIMEOUT_MS, SERVER_VERSION
from beetsplug.beetstreamnext.core.storage.encryption import get_key_hash
from beetsplug.beetstreamnext.core.storage.rekey import RekeyError, rekey_database
from beetsplug.beetstreamnext.utils.system import get_env


BACKUP_FORMAT = 1

DB_MANIFEST_FILE = 'manifest.json'

META_TABLES = frozenset({'db_metadata', 'encryption'})


class BackupError(RuntimeError):
    """A backup could not be created, or an archive can't be restored."""


class BackupKeyMismatchError(BackupError):
    """The backup was made with a different key and no old key was given."""


def _meta(conn: sqlite3.Connection, key: str, table: str = 'db_metadata') -> str | None:
    if table not in META_TABLES:
        raise ValueError(f'Unknown metadata table: {table}')

    query = f'SELECT value FROM {table} WHERE key = ?'  # nosec B608

    try:
        row = conn.execute(query, (key,)).fetchone()

    except sqlite3.DatabaseError:
        return None

    return row[0] if row else None


def _read_db_info(path: Path) -> Dict[str, Any]:
    """Check integrity of database file and read its schema version and key hash."""

    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != 'ok':
                raise BackupError('The database failed its integrity check.')
        except sqlite3.DatabaseError as e:
            raise BackupError(f'Not a valid SQLite database: {e}') from e

        version = _meta(conn, 'version')
        return {
            'db_version': int(version) if version is not None else None,
            'key_hash': _meta(conn, 'key_hash', table='encryption'),
        }
    finally:
        conn.close()


def create_backup(db_path: str | Path, dest: str | Path) -> Path:
    """
    Write a snapshot BeetstreamNext's database to a .tar.gz.
    Encryption not included.
    """

    db_path = Path(db_path)
    dest = Path(dest)

    if not db_path.is_file():
        raise BackupError(f"BeetstreamNext database not found at '{db_path}'.")

    if dest.is_dir():
        dest = dest / f"beetstreamnext-backup-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / DB_FILE_NAME

        src = sqlite3.connect(db_path)
        src.execute(f"PRAGMA busy_timeout = {int(DB_BUSY_TIMEOUT_MS)}")
        dst = sqlite3.connect(snapshot)
        try:
            src.backup(dst)     # Can run online
        finally:
            dst.close()
            src.close()

        info = _read_db_info(snapshot)
        manifest = {
            'format': BACKUP_FORMAT,
            'created_at': time.time(),
            'beetstreamnext_version': SERVER_VERSION,
            'db_version': info['db_version'],
            'key_hash': info['key_hash'],
        }

        manifest_file = Path(tmp) / DB_MANIFEST_FILE
        manifest_file.write_text(json.dumps(manifest, indent=2))

        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + '.part')
        try:
            partial.touch(mode=0o600)
            with tarfile.open(partial, 'w:gz') as tar:
                tar.add(manifest_file, arcname=DB_MANIFEST_FILE)
                tar.add(snapshot, arcname=DB_FILE_NAME)
            partial.replace(dest)
        finally:
            partial.unlink(missing_ok=True)

    return dest


def restore_backup(archive: str | Path, db_path: str | Path, old_key: Optional[str] = None) -> Path | None:
    """
    Replace the database at `db_path` with the one in `archive`. Server must
    be stopped.
    The previous database is kept next to it as '<name>.pre-restore-<timestamp>'
    and its path is returned.
    """

    archive = Path(archive)
    db_path = Path(db_path)

    if not archive.is_file():
        raise BackupError(f"Backup file not found at '{archive}'.")

    with tempfile.TemporaryDirectory(dir=db_path.parent) as tmp:

        tmp = Path(tmp)

        try:
            with tarfile.open(archive, 'r:*') as tar:

                members = {m.name: m for m in tar.getmembers() if m.isfile()}
                if DB_MANIFEST_FILE not in members or DB_FILE_NAME not in members:
                    raise BackupError('Not a BeetstreamNext backup (manifest or database missing).')

                # Read the two known members explicitly: never extract arbitrary archive paths
                manifest = json.loads(tar.extractfile(members[DB_MANIFEST_FILE]).read())
                extracted = tmp / DB_FILE_NAME

                with tar.extractfile(members[DB_FILE_NAME]) as src, extracted.open('wb') as out:
                    shutil.copyfileobj(src, out)

        except (tarfile.TarError, json.JSONDecodeError, OSError) as e:
            raise BackupError(f'Unreadable backup: {e}') from e

        if manifest.get('format') != BACKUP_FORMAT:
            raise BackupError(f"Unsupported backup format: {manifest.get('format')!r}.")

        info = _read_db_info(extracted)

        current_hash = get_key_hash()
        if info['key_hash'] and info['key_hash'] != current_hash:
            if not old_key:
                raise BackupKeyMismatchError(
                    'This backup was made with a different BEETSTREAMNEXT_KEY than the one currently set. '
                    'Set BEETSTREAMNEXT_OLD_KEY to the original key to decrypt it.'
                )

            if info['key_hash'] != get_key_hash(old_key):
                raise BackupError('BEETSTREAMNEXT_OLD_KEY does not match the key this backup was made with.')

            # Re-encrypt the extracted copy (live database is untouched if this fails)
            try:
                rekey_database(extracted, old_key, get_env('BEETSTREAMNEXT_KEY'))
            except RekeyError as e:
                raise BackupError(f'Could not re-encrypt the backup: {e}') from e

        if db_path.is_file():
            current = _read_db_info(db_path)
            if (info['db_version'] or 0) > (current['db_version'] or 0):
                raise BackupError(
                    f"This backup (db schema v{info['db_version']}) is newer than this installation "
                    f"(v{current['db_version']}). Upgrade BeetstreamNext first."
                )

        previous = None
        if db_path.is_file():
            previous = db_path.with_name(f"{db_path.name}.pre-restore-{time.strftime('%Y%m%d-%H%M%S')}")
            db_path.replace(previous)

        # Remove WAL/SHM files from old db to avoid corruption
        for suffix in ('-wal', '-shm'):
            Path(f'{db_path}{suffix}').unlink(missing_ok=True)

        extracted.chmod(0o600)
        extracted.replace(db_path)

    return previous
