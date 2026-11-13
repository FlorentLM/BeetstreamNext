from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path

import flask

from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.library.indexes import ArtistIndex
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.db import library_cache
from beetsplug.beetstreamnext.utils.text import md5_hex, split_beets_multi, validate_mbid



@with_app_context
def credit_keys(name: str) -> list[str]:
    """Keys of a joint credit, or [] if name isn't a multi-artist credit."""

    with flask.g.lib.transaction() as tx:
        rows = tx.query(
            """
            SELECT albumartists, mb_albumartistids FROM albums
            WHERE albumartist = ? COLLATE NOCASE LIMIT 1
            """, (name,)
        )
        if not rows:
            rows = tx.query(
                """
                SELECT artists, mb_artistids FROM items
                WHERE artist = ? COLLATE NOCASE LIMIT 1
                """, (name,)
            )

    if not rows or not IDs.is_joint_credit(rows[0][0], name):
        return []

    return IDs.joint_credit_keys(name, rows[0][0], rows[0][1])


def artist_keys(name: str, mbid: str | None = None) -> list[str]:
    """Keys an image can be stored under for this artist, best first."""

    joint = credit_keys(name)
    if joint:       # Never fall back to the first member's solo mbid
        return joint

    keys = [IDs.encode_artist(name, is_mbid=False)]     # image uploaded before the artist had an mbid
    if mbid:
        keys.insert(0, IDs.encode_artist(mbid, is_mbid=True))
    return keys


def uploaded_image(keys: Sequence[str]) -> bytes | None:
    """Manually uploaded image stored under the first of these keys that has one."""

    if not keys:
        return None

    with database() as db:
        rows = db.execute(
            f"""
            SELECT artist_key, image
            FROM artist_images
            WHERE artist_key IN ({','.join('?' * len(keys))})
            AND image IS NOT NULL
            """, list(keys)  # nosec B608  # noqa: S608 vars are constants
        ).fetchall()

    by_key = {r['artist_key']: r['image'] for r in rows}

    return next((by_key[k] for k in keys if k in by_key), None)


def get_image(key: str) -> bytes | None:

    with database() as db:
        row = db.execute(
            """
            SELECT image 
            FROM artist_images 
            WHERE artist_key = ? AND image IS NOT NULL
            """, (key,)
        ).fetchone()

    return row['image'] if row else None


def set_image(name: str, mbid: str | None, image: bytes) -> None:
    with database() as db:
        db.execute(
            """
            INSERT INTO artist_images (artist_key, name, image, source, uploaded_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(artist_key) DO UPDATE SET
                name = excluded.name, image = excluded.image,
                source = excluded.source, uploaded_at = excluded.uploaded_at,
                matched_name = NULL, checked_at = NULL
            """, (artist_keys(name, mbid)[0], name, image, 'manual', time.time())
        )


def record_status(key: str, name: str, source: str, matched_name: str | None = None) -> None:
    """
    Record outcome of an artist Deezer lookup ('deezer' or 'none').
    """

    matched = matched_name if matched_name and matched_name != name else None

    with database() as db:
        db.execute(
            """
            INSERT INTO artist_images (artist_key, name, image, source, matched_name, checked_at)
            VALUES (?, ?, NULL, ?, ?, ?)
            ON CONFLICT(artist_key) DO UPDATE SET
                name = excluded.name, source = excluded.source,
                matched_name = excluded.matched_name, checked_at = excluded.checked_at
            WHERE artist_images.image IS NULL
            """, (key, name, source, matched, time.time())
        )


def delete_image(key: str) -> bool:

    with database() as db:
        cur = db.execute(
            """
            DELETE FROM artist_images 
            WHERE artist_key = ? AND image IS NOT NULL
            """, (key,)
        )
    return cur.rowcount > 0


def list_images() -> list[dict]:

    with database() as db:
        rows = db.execute(
            """
            SELECT artist_key, name, source, uploaded_at
            FROM artist_images
            WHERE image IS NOT NULL
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()

    result = []
    for r in rows:
        kind = IDs.decode_artist(r['artist_key'])[1]
        if kind == 'hash':
            keys = credit_keys(r['name'])
            kind = 'mbids' if len(keys) > 1 and keys[0] == r['artist_key'] else 'name'
        result.append(dict(r) | {'link': kind})
    return result


def load_stored() -> dict[str, sqlite3.Row]:
    """Every artist_images row (without the blob), by artist key."""

    with database() as db:
        return {
            r['artist_key']: r for r in db.execute(
                """
                SELECT artist_key, name, source, uploaded_at, matched_name, checked_at,
                       image IS NOT NULL AS has_image
                FROM artist_images
                """
            ).fetchall()
        }


ARTIST_FILTERS = ('all', 'custom', 'missing', 'fallback', 'unchecked', 'orphan')
ARTISTS_PER_PAGE = 50


@with_app_context
def list_artists(filter_: str = 'all', q: str = '', page: int = 1, per_page: int = ARTISTS_PER_PAGE) -> dict:
    """
    One row per album artist with the status of the image.
    Returns {'rows', 'counts', 'filter', 'q', 'page', 'pages', 'total'}.
    """

    filter_ = filter_ if filter_ in ARTIST_FILTERS else 'all'
    q = (q or '').strip()

    stored = load_stored()

    entries = []
    claimed: set[str] = set()

    for artist in ArtistIndex.get().album_artists:
        key = next((k for k in artist.keys if k in stored), artist.keys[0])
        row = stored.get(key)
        if row is not None:
            claimed.add(key)

        status, link = 'unchecked', None
        if row is not None:
            if row['has_image']:
                status = 'custom'
                link = artist.link_kind(key)
            elif row['source'] == 'none':
                status = 'missing'
            elif row['source'] == 'deezer':
                status = 'fallback' if row['matched_name'] else 'deezer'

        entries.append({
            'name': artist.name,
            'joint': artist.joint,
            'artist_key': key,
            'status': status,
            'matched_name': row['matched_name'] if row is not None else None,
            'uploaded_at': row['uploaded_at'] if row is not None else None,
            'link': link,
        })

    for key, row in stored.items():
        if row['has_image'] and key not in claimed:
            entries.append({
                'name': row['name'], 'joint': False, 'artist_key': key, 'status': 'orphan',
                'matched_name': None, 'uploaded_at': row['uploaded_at'],
                'link': None,
            })

    needle = q.lower()
    if needle:
        entries = [e for e in entries if needle in e['name'].lower()]

    counts = {f: 0 for f in ARTIST_FILTERS}
    counts['all'] = len(entries)

    for e in entries:
        if e['status'] in counts:   # 'deezer' (a direct match) only counts under 'all'
            counts[e['status']] += 1

    if filter_ != 'all':
        entries = [e for e in entries if e['status'] == filter_]

    entries.sort(key=lambda e: e['name'].lower())

    total = len(entries)
    pages = max(1, -(-total // per_page))
    page = min(max(1, page), pages)
    start = (page - 1) * per_page

    payload = {
        'rows': entries[start:start + per_page],
        'counts': counts,
        'filter': filter_, 'q': q,
        'page': page,
        'pages': pages,
        'total': total
    }

    return payload


def fetched_image_path(name: str) -> Path:
    return app.config['ARTIST_IMAGE_DATA_PATH'] / f'{md5_hex(name)}.jpg'


def image_version(uploaded_at: int | None, name: str) -> int:
    """Version of the image shown for an artist (for cache busting), 0 if there is none."""
    if uploaded_at:
        return int(uploaded_at)
    path = fetched_image_path(name)
    return int(path.stat().st_mtime) if path.is_file() else 0


@with_app_context
def find_artist(name: str) -> tuple[str, str] | None:
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
def suggest_artists(query: str, limit: int = 15) -> list[tuple[str, bool]]:
    """(name, is_joint_credit) pairs of library album artists matching the query."""

    query = (query or '').strip()
    if len(query) < 2:
        return []

    escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

    with flask.g.lib.transaction() as tx:
        rows = tx.query(
            """
            SELECT albumartist, MAX(albumartists) FROM albums
            WHERE albumartist LIKE ? ESCAPE '\\'
            GROUP BY albumartist
            ORDER BY albumartist COLLATE NOCASE LIMIT ?
            """, (f'%{escaped}%', limit)
        )
    return [(r[0], IDs.is_joint_credit(r[1], r[0])) for r in rows if r[0]]


@with_app_context
def library_keys() -> set[str]:
    """
    Every artist key (mbid and name) currently in the beets library.
    """

    keys: set[str] = set()

    def add(names: str | None, mbids: str | None) -> None:
        for n in [names or '', *split_beets_multi(names or '')]:     # Joint credit, then each member
            if n:
                keys.add(IDs.encode_artist(n, is_mbid=False))
        for m in split_beets_multi(mbids or ''):
            if validate_mbid(m):
                keys.add(IDs.encode_artist(m, is_mbid=True))

    sources = (
        ('albums', (('albumartist', 'mb_albumartistid'), ('albumartists', 'mb_albumartistids'))),
        ('items', (('artist', 'mb_artistid'), ('artists', 'mb_artistids'))),
    )

    joint_cols = {
        'albums': ('albumartist', 'albumartists', 'mb_albumartistids'),
        'items': ('artist', 'artists', 'mb_artistids'),
    }

    with flask.g.lib.transaction() as tx:

        for table, column_pairs in sources:
            cols = set(library_cache.schema(table))

            for name_col, mbid_col in column_pairs:

                if name_col in cols and mbid_col in cols:
                    rows = tx.query(
                        f"""
                        SELECT {name_col}, {mbid_col} 
                        FROM {table}
                        """  # nosec B608  # noqa: S608 vars are constants
                    )
                    for row in rows:
                        add(row[0], row[1])

            name_col, names_col, mbids_col = joint_cols[table]
            if {name_col, names_col, mbids_col} <= cols:
                for name, names, mbids in tx.query(f"SELECT {name_col}, {names_col}, {mbids_col} FROM {table}"):  # nosec B608  # noqa: S608 vars are constants
                    if name and IDs.is_joint_credit(names, name):
                        keys.update(IDs.joint_credit_keys(name, names, mbids))
    return keys


@with_app_context
def cleanup_artist_records() -> int:
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
