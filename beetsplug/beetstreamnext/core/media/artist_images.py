from __future__ import annotations

import time
from typing import Optional, List, Tuple
import flask

from beetsplug.beetstreamnext.application import with_app_context
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.db import get_beets_schema
from beetsplug.beetstreamnext.utils.text import split_beets_multi, validate_mbid



def find_uploaded_image(name: str, mbid: Optional[str] = None) -> bytes | None:
    """
    Manually uploaded image for this artist.
    """

    keys = [IDs.encode_artist(name, is_mbid=False)]     # image uploaded before the artist had an mbid
    if mbid:
        keys.insert(0, IDs.encode_artist(mbid))

    with database() as db:
        rows = db.execute(
            f"""
            SELECT artist_key, image 
            FROM artist_images
            WHERE artist_key IN ({','.join('?' * len(keys))}) 
            AND image IS NOT NULL
            """, keys
        ).fetchall()

    by_key = {r['artist_key']: r['image'] for r in rows}

    return next((by_key[k] for k in keys if k in by_key), None)


def get_image(key: str) -> bytes | None:

    with database() as db:
        row = db.execute(
            """
            SELECT image 
            FROM artist_images 
            WHERE artist_key = ?
            """, (key,)
        ).fetchone()

    return row['image'] if row else None


def set_image(name: str, mbid: Optional[str], image: bytes) -> None:
    with database() as db:
        db.execute(
            """
            INSERT INTO artist_images (artist_key, name, image, source, uploaded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(artist_key) DO UPDATE SET
                name = excluded.name, image = excluded.image,
                source = excluded.source, uploaded_at = excluded.uploaded_at
            """, (IDs.encode_artist(mbid or name, is_mbid=bool(mbid)), name, image, 'manual', time.time())
        )


def delete_image(key: str) -> bool:

    with database() as db:
        cur = db.execute(
            """
            DELETE FROM artist_images 
            WHERE artist_key = ?
            """, (key,)
        )
    return cur.rowcount > 0


def list_images() -> List[dict]:

    with database() as db:
        rows = db.execute(
            """
            SELECT artist_key, name, source, uploaded_at
            FROM artist_images
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()

    return [dict(r) | {'has_mbid': IDs.decode_artist(r['artist_key'])[1] == 'mbid'} for r in rows]


@with_app_context
def find_artist(name: str) -> Tuple[str, str] | None:
    """(canonical name, mbid) of an artist from the beets library, or None."""

    name = (name or '').strip()
    if not name:
        return None

    with flask.g.lib.transaction() as tx:
        rows = tx.query(
            """
            SELECT albumartist, mb_albumartistid FROM albums
            WHERE albumartist = ? COLLATE NOCASE LIMIT 1
            """, (name,)
        )
        if not rows:
            rows = tx.query(
                """
                SELECT artist, mb_artistid FROM items
                WHERE artist = ? COLLATE NOCASE LIMIT 1
                """, (name,)
            )

    if not rows:
        return None

    return rows[0][0], validate_mbid(rows[0][1]) or ''


@with_app_context
def suggest_artists(query: str, limit: int = 15) -> List[str]:

    query = (query or '').strip()
    if len(query) < 2:
        return []

    escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

    with flask.g.lib.transaction() as tx:
        rows = tx.query(
            """
            SELECT DISTINCT albumartist FROM albums
            WHERE albumartist LIKE ? ESCAPE '\\'
            ORDER BY albumartist COLLATE NOCASE LIMIT ?
            """, (f'%{escaped}%', limit)
        )
    return [r[0] for r in rows if r[0]]


@with_app_context
def library_keys() -> set[str]:
    """
    Every artist key (mbid and name) currently in the beets library.
    """

    keys: set[str] = set()

    def add(names: Optional[str], mbids: Optional[str]) -> None:
        for n in [names or '', *split_beets_multi(names or '')]:     # Joint credit, then each member
            if n:
                keys.add(IDs.encode_artist(n, is_mbid=False))
        for m in split_beets_multi(mbids or ''):
            if validate_mbid(m):
                keys.add(IDs.encode_artist(m))

    sources = (
        ('albums', (('albumartist', 'mb_albumartistid'), ('albumartists', 'mb_albumartistids'))),
        ('items', (('artist', 'mb_artistid'), ('artists', 'mb_artistids'))),
    )

    with flask.g.lib.transaction() as tx:

        for table, column_pairs in sources:
            cols = set(get_beets_schema(table))

            for name_col, mbid_col in column_pairs:

                if name_col in cols and mbid_col in cols:
                    rows = tx.query(
                        f"""
                        SELECT {name_col}, {mbid_col} 
                        FROM {table}
                        """
                    )
                    for row in rows:
                        add(row[0], row[1])
    return keys


@with_app_context
def remove_orphan_images() -> int:
    """
    Deletes images of artists that are no longer in the library.
    """

    with database() as db:

        result = db.execute(
            """
            SELECT artist_key 
            FROM artist_images
            """
        ).fetchall()

        stored = [r['artist_key'] for r in result]
        if not stored:
            return 0

        present = library_keys()
        if not present:     # Library can't be read: don't wipe everything
            return 0

        orphans = [k for k in stored if k not in present]
        for key in orphans:
            db.execute(
                """
                DELETE FROM artist_images 
                WHERE artist_key = ?
                """, (key,)
            )

    return len(orphans)
