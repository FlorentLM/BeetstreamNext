from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.library.beets.import_results import album_label
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.storage.connection import database

HISTORY_MAX_ROWS = 100

VISIBLE_ALBUMS = 4
MAX_EXTRA_GROUPS = 6


##
# Recording

def _subfolders(record: Dict[str, Any]) -> Set[str]:
    return set(record.get('folders') or filter(None, [record.get('path')]))


def _merge_imports(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Beets imports a multi-disc album disc by disc, so each merge with a previous disc added,
    the same album is reported with growing count.

    Only the last report should be kept.
    """
    folder_sets = [_subfolders(r) for r in records]

    kept = []
    for i, record in enumerate(records):

        superseded = any(
            folder_sets[i] < other                      # Contained in a bigger report
            or (folder_sets[i] == other and j > i)      # or reported again later
            for j, other in enumerate(folder_sets) if j != i
        )

        if not superseded:
            kept.append(record)

    return kept


def _row(run_path: str, outcome: str, record: Dict[str, Any], ts: float) -> Dict[str, Any]:
    return {
        'ts': ts,
        'run_path': run_path,
        'outcome': outcome,
        'cause': record.get('cause'),
        'reason': record.get('reason'),
        'artist': record.get('artist') or '',
        'album': record.get('album') or '',
        'path': record.get('path') or run_path,
        'tracks': record.get('tracks'),
    }


def _drop_superseded(db: sqlite3.Connection, settled: set[str], skipped_again: set[str]) -> None:
    """Remove the rows that reimport will replace (imported or skipped again)."""

    skipped = db.execute(
        """
        SELECT id, path FROM import_history
        WHERE outcome = 'skipped'
        """
    ).fetchall()

    superseded = [
        (r['id'],) for r in skipped
        if r['path'] in skipped_again or any(Path(r['path']).is_relative_to(folder) for folder in settled)
    ]
    db.executemany(
        """
        DELETE FROM import_history
        WHERE id = ?
        """, superseded
    )


def record_run(run_path: str, results: Dict[str, List[Dict[str, Any]]]) -> int:
    """Store a finished run's results. Returns how many rows were added."""

    now = time.time()

    imported = _merge_imports(results.get('imported', []))
    settled = {folder for record in imported for folder in _subfolders(record)}

    # Only latest report per skipped folder, no report for folders that are imported
    # (a task that beets replaced with a merged one is reported as skipped)
    skipped: Dict[str, Dict[str, Any]] = {}

    for record in results.get('skipped', []):
        path = record.get('path') or run_path

        if not any(Path(path).is_relative_to(folder) for folder in settled):
            skipped[path] = record

    rows = [_row(run_path, 'imported', r, now) for r in imported]
    rows += [_row(run_path, 'skipped', r, now) for r in skipped.values()]
    if not rows:
        return 0

    try:
        with app.app_context(), database() as db:

            _drop_superseded(db, settled, set(skipped))

            db.executemany(
                """
                INSERT INTO import_history (ts, run_path, outcome, cause, reason, artist, album, path, tracks)
                VALUES (:ts, :run_path, :outcome, :cause, :reason, :artist, :album, :path, :tracks)
                """, rows
            )

            db.execute(
                """
                DELETE FROM import_history
                WHERE id NOT IN (SELECT id FROM import_history ORDER BY ts DESC, id DESC LIMIT ?)
                """, (HISTORY_MAX_ROWS,)
            )

    except Exception as e:
        bsn_logger.error(f'Could not store the import history: {e}')
        return 0

    return len(rows)


##
# Reading

def reimport_parent(entry: Any) -> Optional[str]:

    album, run = Path(entry['path']), Path(entry['run_path'])
    parent = album.parent

    if parent == album or not parent.is_relative_to(run):
        return None

    return str(parent) if parent.is_dir() else None


def _shared_parents() -> Dict[str, int]:
    """Parent folders with more than one skipped subfolders -> how many"""

    with database() as db:
        rows = db.execute(
            """
            SELECT path, run_path FROM import_history
            WHERE outcome = 'skipped'
            """
        ).fetchall()

    counts: Dict[str, int] = {}
    for r in rows:
        if parent := reimport_parent(r):
            counts[parent] = counts.get(parent, 0) + 1

    return {parent: n for parent, n in counts.items() if n > 1}


def list_history(outcome: Optional[str] = None, limit: int = HISTORY_MAX_ROWS) -> List[Dict[str, Any]]:
    """Newest first. Called inside an app context."""
    where, params = ('WHERE outcome = ?', (outcome,)) if outcome in ('imported', 'skipped') else ('', ())

    with database() as db:
        rows = db.execute(
            f"""
            SELECT * FROM import_history {where}
            ORDER BY ts DESC, id DESC LIMIT ?
            """, (*params, limit),  # nosec B608
        ).fetchall()

    shared = _shared_parents()

    entries = []
    for r in rows:
        parent = reimport_parent(r)
        entries.append({
            **dict(r), 'label': album_label(dict(r)),
            'when': datetime.fromtimestamp(r['ts']).strftime('%Y-%m-%d %H:%M'),
            'exists': Path(r['path']).is_dir(),
            'parent': parent if r['outcome'] == 'skipped' and parent in shared else None,
            'siblings': shared.get(parent, 0),
        })

    return entries


def get_entry(entry_id: int) -> Optional[Dict[str, Any]]:

    with database() as db:
        row = db.execute(
            """
            SELECT * FROM import_history
            WHERE id = ?
            """, (entry_id,)
        ).fetchone()

    return dict(row) if row else None


##
# Display

def group_rows(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Albums sharing a parent folder are gathered under one header row.
    """
    rows: List[Dict[str, Any]] = []
    done = set()

    for entry in entries:
        parent = entry['parent']

        if not parent:
            rows.append({'kind': 'album', 'entry': entry, 'grouped': False})
            continue

        if parent in done:
            continue
        done.add(parent)

        members = [e for e in entries if e['parent'] == parent]

        rows.append({'kind': 'group', 'parent': parent, 'count': len(members), 'entry_id': members[0]['id']})
        rows.extend({'kind': 'album', 'entry': e, 'grouped': True} for e in members)

    return rows


def visible_groups(rows: List[Dict[str, Any]]) -> int:

    albums = groups = 0

    for row in rows:
        if row['kind'] == 'album':
            albums += 1
            if albums > VISIBLE_ALBUMS:
                break
        else:
            groups += 1

    return min(groups, MAX_EXTRA_GROUPS)


##
# Removing

def delete_entry(entry_id: int) -> None:
    with database() as db:
        db.execute(
            """
            DELETE FROM import_history
            WHERE id = ?
            """, (entry_id,)
        )


def clear_history(outcome: Optional[str] = None) -> None:

    with database() as db:

        if outcome in ('imported', 'skipped'):
            db.execute(
                """
                DELETE FROM import_history
                WHERE outcome = ?
                """, (outcome,)
            )
        else:
            db.execute(
                """
                DELETE FROM import_history
                """
            )
