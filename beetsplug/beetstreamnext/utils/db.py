import os
import threading
from sqlite3 import Connection
from typing import Dict, Any, List, Optional

import flask
from beets.dbcore.db import Transaction


##
# Beets' database access utilities

_schema_cache: Dict[str, Any] = {}
_schema_lock = threading.Lock()

_beets_table_names = frozenset(['items', 'albums'])


def get_beets_schema(table_name: str = 'items') -> List[str]:
    """Returns column names for the beets db, invalidating the cache if the beets db has changed."""

    if table_name not in _beets_table_names:
        raise AttributeError(f"Table {table_name} does not exist in Beets' db.")

    lib_path = flask.g.lib.path
    current_mtime = os.path.getmtime(os.fsdecode(lib_path))
    cache_key = f'schema_{table_name}'

    with _schema_lock:
        if _schema_cache.get('_mtime') != current_mtime:
            _schema_cache.clear()
            _schema_cache['_mtime'] = current_mtime

        if cache_key in _schema_cache:
            return _schema_cache[cache_key]

    # Query outside lock to avoid holding during IO
    with flask.g.lib.transaction() as tx:
        # SQLite PRAGMA doesnt support bound parameters for table name
        cursor = tx.query(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor]

    with _schema_lock:
        # cache only if mtime hasn't changed while querying
        if _schema_cache.get('_mtime') == current_mtime:
            _schema_cache[cache_key] = columns

    return columns


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


def escape_like(s: str, escape: str = '!') -> str:
    """Escape SQL LIKE wildcards. Use with `LIKE ? ESCAPE '!'`."""
    return s.replace(escape, escape * 2).replace('%', escape + '%').replace('_', escape + '_')
