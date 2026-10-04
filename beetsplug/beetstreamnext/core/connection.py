from __future__ import annotations

from typing import Any, Optional
import os
import sqlite3
from pathlib import Path
import flask

from beetsplug.beetstreamnext.constants import DB_BUSY_TIMEOUT_MS


def database() -> sqlite3.Connection:
    """Get internal database connection."""
    if 'db' not in flask.g:
        flask.g.db = sqlite3.connect(flask.current_app.config['BSN_DB_PATH'])
        flask.g.db.execute("""PRAGMA main.journal_mode = WAL;""")
        flask.g.db.execute("""PRAGMA synchronous = NORMAL;""")
        flask.g.db.execute(f"""PRAGMA busy_timeout = {int(DB_BUSY_TIMEOUT_MS)};""")
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
