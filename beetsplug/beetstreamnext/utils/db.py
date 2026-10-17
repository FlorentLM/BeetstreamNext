from __future__ import annotations

import os
import threading
from sqlite3 import Connection
from typing import Dict, Hashable, Any, List, Optional, Tuple

import flask
from beets.dbcore.db import Transaction

from beetsplug.beetstreamnext.constants import BEETS_MULTI_DELIM


##
# Beets' database access utilities


class SchemaCache:
    """
    Thread-safe cache of the beets db schema. Clear itself when mtime
    of the db changes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stamp: Hashable = None
        self._data: Dict[Hashable, Any] = {}

    def _sync(self, stamp: Hashable) -> None:
        """Drop everything if `stamp` has changed."""
        if self._stamp != stamp:
            self._data.clear()
            self._stamp = stamp

    def get(self, stamp: Hashable, key: Hashable) -> Any:
        with self._lock:
            self._sync(stamp)
            return self._data.get(key)

    def put(self, stamp: Hashable, key: Hashable, value: Any) -> None:
        with self._lock:
            if self._stamp == stamp:
                self._data[key] = value

    def bucket(self, stamp: Hashable, name: Hashable) -> Dict[Any, Any]:
        """Dict private to `name`."""
        with self._lock:
            self._sync(stamp)
            return self._data.setdefault(name, {})


schema_cache = SchemaCache()


_beets_table_names = frozenset(['items', 'albums'])


def get_beets_schema(table_name: str = 'items') -> List[str]:
    """Returns column names for the beets db, invalidating the cache if the beets db has changed."""

    if table_name not in _beets_table_names:
        raise AttributeError(f"Table {table_name} does not exist in Beets' db.")

    lib_path = flask.g.lib.path
    current_mtime = os.path.getmtime(os.fsdecode(lib_path))
    cached = schema_cache.get(current_mtime, table_name)
    if cached is not None:
        return cached

    # Query outside lock to avoid holding during IO
    with flask.g.lib.transaction() as tx:
        # SQLite PRAGMA doesnt support bound parameters for table name
        cursor = tx.query(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor]

    schema_cache.put(current_mtime, table_name, columns)    # dropped if the db changed while querying
    return columns


_lib_cache = SchemaCache()


def _library_stamp() -> Tuple:
    path = os.fsdecode(flask.g.lib.path)
    mtimes = []
    for p in (path, path + '-wal'):
        try:
            mtimes.append(os.path.getmtime(p))
        except OSError:
            mtimes.append(None)
    return (path, *mtimes)


def library_cache(name: str) -> Dict[Any, Any]:
    """
    Process-wide dict for things derived from the beets library, shared across requests.
    """
    return _lib_cache.bucket(_library_stamp(), name)


def chunked_query(
        db_obj: 'Transaction | Connection',
        query_template: str,
        chunked_values: List[Any],
        base_params: Optional[List[Any]] = None,
        chunk_size=900
    ) -> List[Any]:
    """
    db_obj: The beets Transaction or sqlite Connection object
    query_template: SQL string with a '{q}' placeholder for the IN clause
    chunked_values: The list of values to query
    base_params: Static parameters to bind before the chunked values
    """
    base_params = base_params or []
    results = []

    for i in range(0, len(chunked_values), chunk_size):
        chunk = chunked_values[i: i + chunk_size]
        question_marks = ','.join(['?'] * len(chunk))
        sql = query_template.replace('{q}', question_marks)
        params = base_params + chunk

        if isinstance(db_obj, Transaction):
            chunk_results = list(db_obj.query(sql, params))
        else:
            chunk_results = db_obj.execute(sql, params).fetchall()
        results.extend(chunk_results)
    return results


def chunked_execute(
        db_obj: 'Connection',
        query_template: str,
        chunked_values: List[Any],
        trailing_params: Optional[List[Any]] = None,
        chunk_size: int = 900
    ) -> int:
    """
    db_obj: The beets Transaction or sqlite Connection object
    query_template: SQL string with a '{q}' placeholder for the IN clause
    trailing_params: Static parameters to bind after the chunked values
    """
    trailing_params = trailing_params or []
    affected = 0

    for i in range(0, len(chunked_values), chunk_size):
        chunk = list(chunked_values[i: i + chunk_size])
        sql = query_template.replace('{q}', ','.join(['?'] * len(chunk)))
        affected += max(db_obj.execute(sql, chunk + trailing_params).rowcount, 0)

    return affected


def escape_like(s: str, escape: str = '!') -> str:
    """Escape SQL LIKE wildcards. Use with `LIKE ? ESCAPE '!'`."""
    return s.replace(escape, escape * 2).replace('%', escape + '%').replace('_', escape + '_')


def sql_conditions_for(name: str, name_fields: List[str]) -> Tuple[List[str], List[str]]:
    """
    Build OR-conditions and params matching `name` across all name columns.
    `artists`, `composers` and `lyricists` are multi-value beets fields with delimiters, everything else is exact-matched.
    """

    conditions = []
    params = []
    escaped = escape_like(name)
    delim = BEETS_MULTI_DELIM

    for field in name_fields:
        if field in ('artists', 'composers', 'lyricists'):
            # Four shapes: just the value, first, last, or somewhere in the middle
            conditions.extend([
                f"{field} = ?",
                f"{field} LIKE ? ESCAPE '!'",
                f"{field} LIKE ? ESCAPE '!'",
                f"{field} LIKE ? ESCAPE '!'",
            ])
            params.extend([
                name,
                f"{escaped}{delim}%",
                f"%{delim}{escaped}",
                f"%{delim}{escaped}{delim}%",
            ])
        else:
            # artist / composer / lyricist are single-valued in older beets schema
            conditions.append(f"{field} = ?")
            params.append(name)
    return conditions, params
