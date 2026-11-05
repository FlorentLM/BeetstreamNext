from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import flask

from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.library.indexes import AlbumArtistIndex
from beetsplug.beetstreamnext.core.media.images import safe_open_image
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.external.deezer import query_deezer, DeezerUnavailable
from beetsplug.beetstreamnext.core.services.external.session import capped_image_fetch
from beetsplug.beetstreamnext.core.services.events import admin_events
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.concurrency import SingleAccess
from beetsplug.beetstreamnext.utils.db import get_beets_schema
from beetsplug.beetstreamnext.utils.text import split_beets_multi, validate_mbid



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


def find_uploaded_image(name: str, mbid: str | None = None) -> bytes | None:
    """
    Manually uploaded image for this artist.
    """

    keys = artist_keys(name, mbid)

    with database() as db:
        rows = db.execute(
            f"""
            SELECT artist_key, image 
            FROM artist_images
            WHERE artist_key IN ({','.join('?' * len(keys))}) 
            AND image IS NOT NULL
            """, keys  # nosec B608  # noqa: S608 vars are constants
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

    with database() as db:
        stored = {
            r['artist_key']: r for r in db.execute(
                """
                SELECT artist_key, name, source, uploaded_at, matched_name, checked_at,
                       image IS NOT NULL AS has_image
                FROM artist_images
                """
            ).fetchall()
        }

    entries = []
    claimed: set[str] = set()

    for artist in AlbumArtistIndex.get().artists:
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

    page_rows = entries[start:start + per_page]
    for row in page_rows:
        row['image_version'] = image_version(row['uploaded_at'], row['name'])

    payload = {
        'rows': page_rows,
        'counts': counts,
        'filter': filter_, 'q': q,
        'page': page,
        'pages': pages,
        'total': total
    }

    return payload


def image_version(uploaded_at: int | None, name: str) -> int:
    """Version of the iage (for cache busting)."""
    if uploaded_at:
        return int(uploaded_at)
    path = ArtistImageFetcher.image_path(name)
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
            cols = set(get_beets_schema(table))

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


class ArtistImageFetcher:
    """
    Fetches artist images from Deezer: single artist on demand, or all artists in a background run.
    """

    RETRY_AFTER = 30 * 24 * 3600        # Seconds before retrying an artist
    LOOKUP_PAUSE = 0.25                 # Seconds between Deezer requests
    MAX_CONSECUTIVE_FAILURES = 5
    PUBLISH_EVERY = 5                   # live progress updates every n artist

    def __init__(self) -> None:
        self._lock = SingleAccess()
        self.done = self.total = self.found = self.fallback = self.missing = 0
        self.aborted = ''
        self.finished = False           # A run finished since startup

    @property
    def running(self) -> bool:
        return self._lock.running

    @staticmethod
    def image_path(name: str) -> Path:
        return app.config['ARTIST_IMAGE_DATA_PATH'] / f"{hashlib.md5(name.encode('utf-8'), usedforsecurity=False).hexdigest()}.jpg"

    def lookup(self,
            key: str,
            name: str,
            candidates: tuple[str, ...],
            pause: float = 0.0
        ) -> tuple[tuple[str, dict] | None, bool]:
        """
        Try each candidate name on Deezer, save the image of the 1st match and record the outcome.
        """
        match = None
        failed = False

        for candidate in candidates:
            try:
                dz_data = query_deezer(artist=candidate)
            except DeezerUnavailable:
                failed = True
                continue
            finally:
                if pause:
                    time.sleep(pause)

            if dz_data and dz_data.get('type', '') == 'artist':
                match = candidate, dz_data
                break

        if match:
            self._save_image(name, match[1])

        if not failed:
            try:
                if match:
                    record_status(key, name, 'deezer', matched_name=match[0])
                else:
                    record_status(key, name, 'none')
            except Exception as e:
                bsn_logger.warning(f"Could not record image status of '{name}': {e}")

        return match, not failed

    def _save_image(self, name: str, dz_data: dict) -> None:

        path = self.image_path(name)
        if not app.config['save_artists_images'] or path.is_file():
            return

        key = next(filter(dz_data.get, ('picture_xl', 'picture_big', 'picture_medium', 'picture', 'picture_small')), None)
        if not key:
            return

        try:
            content = capped_image_fetch(str(dz_data[key]), timeout=5)
            if content:
                safe_open_image(content).save(path)
        except Exception as e:
            bsn_logger.warning(f"Failed to fetch/save artist image for '{name}' from Deezer: {e}")

    def start(self) -> tuple[bool, str]:
        """
        Fetches images for all artists that have no custom image (and weren't tried recently)
        in a background thread.
        """
        if not app.config['fetch_artists_images']:
            return False, 'Fetching is off in the settings.'

        if not self._lock.try_acquire():
            return False, 'Already running.'

        threading.Thread(target=self._run, daemon=True).start()
        return True, 'Started.'

    @with_app_context
    def _run(self) -> None:
        try:
            with database() as db:
                stored = {
                    r['artist_key']: r for r in db.execute(
                        """
                        SELECT artist_key, image IS NOT NULL AS has_image, checked_at
                        FROM artist_images
                        """
                    ).fetchall()
                }

            cutoff = time.time() - self.RETRY_AFTER
            todo = []

            for artist in AlbumArtistIndex.get().artists:
                row = next((stored[k] for k in artist.keys if k in stored), None)
                if row and (row['has_image'] or (row['checked_at'] or 0) > cutoff):
                    continue
                todo.append(artist)

            self.done = self.found = self.fallback = self.missing = 0
            self.total = len(todo)
            self.aborted = ''
            self.finished = False

            admin_events.publish('artist-fetch', '')

            failures = 0
            for artist in todo:
                match, reliable = self.lookup(
                    key=artist.keys[0],
                    name=artist.name,
                    candidates=artist.candidates,
                    pause=self.LOOKUP_PAUSE
                )

                if not reliable:
                    failures += 1
                    if failures >= self.MAX_CONSECUTIVE_FAILURES:
                        self.aborted = 'Deezer is not responding.'
                        break
                else:
                    failures = 0
                    if not match:
                        self.missing += 1
                    elif match[0] == artist.name:
                        self.found += 1
                    else:
                        self.fallback += 1

                self.done += 1
                if self.done % self.PUBLISH_EVERY == 0:
                    admin_events.publish('artist-fetch', '')

            self.finished = True
            bsn_logger.info(
                f"Artist image fetch complete: {self.done}/{self.total} artists, {self.found} found on Deezer, "
                f"{self.fallback} using a member's image, {self.missing} missing."
                + (f" Stopped: {self.aborted}" if self.aborted else '')
            )
        except Exception as e:
            self.aborted = 'Failed, see the server log.'
            bsn_logger.error(f"Artist image fetch failed: {e}")
        finally:
            self._lock.release()
            admin_events.publish('artist-fetch', '')

    @with_app_context
    def fetch_one(self, name: str) -> dict | None:
        """
        Fetches one artist image from Deezer.
        """
        found = find_artist(name)
        if not found:
            return None

        name, mbid = found
        entry = AlbumArtistIndex.get().by_name.get(name)
        key = entry.keys[0] if entry else artist_keys(name, mbid)[0]
        candidates = entry.candidates if entry else (name,)

        match, reliable = self.lookup(key, name, candidates)
        admin_events.publish('artist-fetch', '')

        if not reliable:
            status = 'unavailable'
        elif not match:
            status = 'missing'
        else:
            status = 'deezer' if match[0] == name else 'fallback'

        return {'name': name, 'status': status, 'matched_name': match[0] if match else None}


artist_image_fetcher = ArtistImageFetcher()
