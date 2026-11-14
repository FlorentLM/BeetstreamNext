from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from sqlite3 import Connection
from typing import Any, Literal, overload

import flask
from beets.dbcore.db import Transaction

from beetsplug.beetstreamnext.constants import BEETS_MULTI_DELIM


##
# Beets' database access utilities


class LibraryCache:
    """
    Process-wide dicts for stuff derived from the beets library.
    Dicts are dropped when the db changes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stamp: tuple = ()
        self._buckets: dict[str, dict[Any, Any]] = {}

    @staticmethod
    def stamp() -> tuple:
        """Path and mtimes of the beets db and its WAL."""
        path = os.fsdecode(flask.g.lib.path)
        return path, *(os.stat(p).st_mtime if os.path.exists(p) else None for p in (path, path + '-wal'))

    def __call__(self, name: str) -> dict[Any, Any]:

        stamp = self.stamp()

        with self._lock:
            if stamp != self._stamp:
                self._buckets.clear()
                self._stamp = stamp

            return self._buckets.setdefault(name, {})

    @contextmanager
    def silent_write(self, *stale: str) -> Iterator[None]:
        """
        For BSN's writes that no cache depends on, to avoid invalidating indexes.
        """
        before = self.stamp()
        yield
        with self._lock:
            if before == self._stamp:
                self._stamp = self.stamp()
                for name in stale:
                    self._buckets.pop(name, None)

    def tables(self) -> list[str]:
        """Names of all the tables in the beets db."""

        cache = self('tables')
        if 'names' not in cache:
            with flask.g.lib.transaction() as tx:
                cache['names'] = [row[0] for row in tx.query(
                    """
                    SELECT name 
                    FROM sqlite_master 
                    WHERE type = 'table' AND name NOT LIKE 'sqlite!_%' ESCAPE '!' ORDER BY name"""
                )]

        return list(cache['names'])

    @overload
    def schema(self, table_name: str = 'items', with_types: Literal[False] = False) -> list[str]: ...

    @overload
    def schema(self, table_name: str, with_types: Literal[True]) -> dict[str, str]: ...

    def schema(self, table_name: str = 'items', with_types: bool = False) -> list[str] | dict[str, str]:
        """Column names of a table of the beets db (or a {name: declared sqlite type} dict if `with_types`)."""

        if table_name not in self.tables():
            raise AttributeError(f"Table {table_name} does not exist in Beets' db.")

        cache = self('schema')
        if table_name not in cache:
            with flask.g.lib.transaction() as tx:
                # SQLite PRAGMA doesnt support bound parameters for table name (checked against the db's tables above)
                cache[table_name] = {row[1]: (row[2] or '').upper() for row in tx.query(f'PRAGMA table_info({quote_identifier(table_name)})')}

        return dict(cache[table_name]) if with_types else list(cache[table_name])


library_cache = LibraryCache()


def chunked_query(
        db_obj: Transaction | Connection,
        query_template: str,
        chunked_values: list[Any],
        base_params: list[Any] | None = None,
        chunk_size=900
    ) -> list[Any]:
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
        db_obj: Connection,
        query_template: str,
        chunked_values: list[Any],
        trailing_params: list[Any] | None = None,
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


def quote_identifier(name: str) -> str:
    """Just a quoting for a table or column name for SQL."""
    return '"' + name.replace('"', '""') + '"'


def escape_like(s: str, escape: str = '!') -> str:
    """Escape SQL LIKE wildcards. Use with `LIKE ? ESCAPE '!'`."""
    return s.replace(escape, escape * 2).replace('%', escape + '%').replace('_', escape + '_')


def sql_conditions_for(name: str, name_fields: list[str]) -> tuple[list[str], list[str]]:
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
