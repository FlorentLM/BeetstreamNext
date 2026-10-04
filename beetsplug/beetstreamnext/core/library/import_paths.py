from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.storage.connection import database


def list_pinned_paths() -> list[dict]:

    with database() as db:
        rows = db.execute(
            """
            SELECT id, path, incremental, watch, last_triggered
            FROM pinned_import_paths 
            ORDER BY path
            """
        ).fetchall()

    return [
        {
            'id': r['id'], 'path': r['path'], 'incremental': bool(r['incremental']),
            'watch': bool(r['watch']),
            'last_triggered': (datetime.fromtimestamp(r['last_triggered']).strftime('%Y-%m-%d %H:%M')
                               if r['last_triggered'] else None),
        }
        for r in rows
    ]


def validate_pinned_path(candidate: Path) -> Optional[str]:
    """Returns an error message if the path can't be used for a non-interactive import."""
    if not candidate.is_absolute():
        return 'Use an absolute path.'

    if not candidate.is_dir():
        return f"'{candidate}' is not a directory."

    # Non-interactive import must never touch the formatted library
    path_a, path_b = candidate.resolve(), app.config['root_directory'].resolve()

    if path_a.is_relative_to(path_b) or path_b.is_relative_to(path_a):
        return 'Path must not overlap the formatted music directory (be inside it, or contain it).'

    return None


def add_pinned_path(path: str, incremental: bool = True) -> Tuple[bool, str]:
    candidate = Path(path).expanduser() if path else None
    if not candidate:
        return False, 'Enter a directory path.'

    error = validate_pinned_path(candidate)
    if error:
        return False, error

    with database() as db:
        cur = db.execute(
            """INSERT OR IGNORE INTO pinned_import_paths (path, incremental) VALUES (?, ?)""",
            (str(candidate), int(incremental)),
        )
    return (True, '') if cur.rowcount else (False, 'That path is already pinned.')


def remove_pinned_path(path_id: int) -> None:
    with database() as db:
        db.execute(
            """
            DELETE FROM pinned_import_paths 
            WHERE id = ?
            """, (path_id,)
        )


def set_pinned_incremental(path_id: int, incremental: bool) -> None:
    with database() as db:
        db.execute(
            """
            UPDATE pinned_import_paths 
            SET incremental = ? 
            WHERE id = ?
            """, (int(incremental), path_id)
        )


def set_pinned_watch(path_id: int, watch: bool) -> None:
    with database() as db:
        db.execute(
            """
            UPDATE pinned_import_paths 
            SET watch = ? 
            WHERE id = ?
            """, (int(watch), path_id)
        )


def mark_pinned_triggered(path_id: int) -> None:
    with database() as db:
        db.execute(
            """
            UPDATE pinned_import_paths 
            SET last_triggered = unixepoch() 
            WHERE id = ?
            """, (path_id,)
        )
