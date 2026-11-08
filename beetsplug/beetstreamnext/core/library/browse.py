from __future__ import annotations

from typing import Any
import flask

from beetsplug.beetstreamnext.constants import BEETS_MULTI_DELIM
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.utils.db import escape_like, library_cache, quote_identifier
from beetsplug.beetstreamnext.utils.text import split_beets_multi


MAX_PAGE_SIZE = 200
MAX_SEARCH_WORDS = 4
MAX_FILTERS = 20

# Tabulator filters
_COMPARISONS = {'=', '!=', '<', '<=', '>', '>='}
_LIKE_PATTERNS = {'like': '%{}%', 'starts': '{}%', 'ends': '%{}'}


class LibraryView:
    """
    Read-only view of the beets library including flexible attributes.
    """

    def __init__(self) -> None:

        cache = library_cache('library_view')

        if not cache:
            items = library_cache.schema('items', with_types=True)
            albums = library_cache.schema('albums', with_types=True)

            with flask.g.lib.transaction() as tx:
                item_keys = [r[0] for r in tx.query('SELECT DISTINCT key FROM item_attributes ORDER BY key')]
                album_keys = [r[0] for r in tx.query('SELECT DISTINCT key FROM album_attributes ORDER BY key')]

            # name -> (SQL expression, declared type)
            columns = {('item_id' if name == 'id' else name): (f'i.{quote_identifier(name)}', sql_type) for name, sql_type in items.items()}
            columns |= {f'albums.{name}': (f'a.{quote_identifier(name)}', sql_type) for name, sql_type in albums.items() if name != 'id'}
            columns |= {
                f'attr.{key}': ('(SELECT value FROM item_attributes WHERE entity_id = i.id AND key = ?)', 'TEXT')
                for key in item_keys
            }
            columns |= {
                f'albums.attr.{key}': ('(SELECT value FROM album_attributes WHERE entity_id = i.album_id AND key = ?)', 'TEXT')
                for key in album_keys
            }

            cache['columns'] = {name: sql_type for name, (_, sql_type) in columns.items()}
            cache['source'] = (
                'SELECT ' + ', '.join(f'{expr} AS {quote_identifier(name)}' for name, (expr, _) in columns.items()) +  # nosec B608  # noqa: S608 identifiers come from the schema
                ' FROM items i LEFT JOIN albums a ON a.id = i.album_id'
            )
            cache['params'] = [name.rsplit('attr.', 1)[1] for name, (expr, _) in columns.items() if '?' in expr]

            # Columns with at least one value
            with flask.g.lib.transaction() as tx:
                flags = tx.query(
                    'SELECT ' + ', '.join(f"MAX({quote_identifier(n)} IS NOT NULL AND {quote_identifier(n)} != '')" for n in columns) +  # nosec B608  # noqa: S608 identifiers come from the schema
                    f" FROM ({cache['source']})",
                    cache['params']
                )[0]
            cache['populated'] = {name for name, flag in zip(columns, flags, strict=True) if flag}

        self.columns: dict[str, str] = cache['columns']     # name -> col type
        self.source: str = cache['source']
        self.params: list[str] = cache['params']            # Attribute keys (in order of the `?`)
        self.non_empty: set[str] = cache['populated']

    def rows(self,
            q: str = '',
            filters: tuple[tuple[str, str, str], ...] = (),   # (column, filter, value)
            sort: str = '',
            descending: bool = False,
            page: int = 1,
            size: int = 50
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of rows and number of rows matching."""

        conditions: list[str] = []
        params: list[Any] = []

        for word in q.split()[:MAX_SEARCH_WORDS]:
            conditions.append('(' + ' OR '.join(
                f"lower(CAST({quote_identifier(column)} AS TEXT)) LIKE ? ESCAPE '!'" for column in self.columns
            ) + ')')
            params += ['%' + escape_like(word.lower()) + '%'] * len(self.columns)

        for column, kind, value in filters[:MAX_FILTERS]:
            if column not in self.columns or not (kind in _COMPARISONS or kind in _LIKE_PATTERNS):
                raise ValueError(f'Invalid filter: {column} {kind}')

            if kind in _LIKE_PATTERNS:
                conditions.append(f"lower(CAST({quote_identifier(column)} AS TEXT)) LIKE ? ESCAPE '!'")
                params.append(_LIKE_PATTERNS[kind].format(escape_like(value.lower())))
            else:
                conditions.append(f'{quote_identifier(column)} {kind} ?')
                params.append(value)

        where = (' WHERE ' + ' AND '.join(conditions)) if conditions else ''

        order = 'item_id'
        if sort in self.columns:
            collate = ' COLLATE NOCASE' if 'TEXT' in self.columns[sort] else ''
            order = f"{quote_identifier(sort)}{collate} {'DESC' if descending else 'ASC'}, item_id"

        size = max(1, min(size, MAX_PAGE_SIZE))
        source_params = self.params

        with flask.g.lib.transaction() as tx:

            total = tx.query(
                f'SELECT COUNT(*) FROM ({self.source}){where}',   # nosec B608  # noqa: S608 identifiers come from the schema
                [*source_params, *params]
            )[0][0]

            rows = tx.query(
                f'SELECT * FROM ({self.source}){where} ORDER BY {order} LIMIT ? OFFSET ?',   # nosec B608  # noqa: S608
                [*source_params, *params, size, (max(1, page) - 1) * size]
            )

        data = []
        for row in rows:
            record = {}

            for name, value in zip(self.columns, row, strict=True):

                if isinstance(value, bytes):
                    value = value.decode('utf-8', 'replace')

                if isinstance(value, str) and BEETS_MULTI_DELIM in value:
                    value = ', '.join(split_beets_multi(value))

                record[name] = value

            data.append({'id': IDs.encode_song({**record, 'id': record['item_id']}), **record})

        return data, total
