import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List

from beetsplug.beetstreamnext.constants import CLEANUP_INTERVAL_SEC, MAX_CACHE_AGE_DAYS
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.security import rate_limiter


_cleanup_lock = threading.Lock()
_last_cleanup: float = 0.0


def cache_disk_usage(thumb_dir: str | Path, http_cache: str | Path) -> int:
    """Total bytes currently used by the thumbnail and HTTP caches on disk."""
    total = 0

    thumb_dir = Path(thumb_dir)
    if thumb_dir.exists():
        for f in thumb_dir.iterdir():
            if f.is_file():
                total += f.stat().st_size

    http_cache = Path(http_cache)
    if http_cache.exists():
        total += http_cache.stat().st_size

    return total


def clear_caches(thumb_dir: str | Path, http_cache: str | Path) -> List[str]:
    """Clears thumbnails and HTTP cache. Returns a list of what was cleared."""
    cleared = []

    thumb_dir = Path(thumb_dir)
    http_cache = Path(http_cache)

    # Thumbnails
    if thumb_dir.exists():
        try:
            n = 0
            for f in thumb_dir.iterdir():
                if f.is_file() and f.suffix == '.jpg':
                    f.unlink(missing_ok=True)
                    n += 1
            if n > 0:
                cleared.append(f'{n} thumbnail(s)')
        except Exception as e:
            bsn_logger.error(f'Thumbnail cache clear failed: {e}')
            raise RuntimeError(f'Error clearing thumbnail cache: {e}')

    # HTTP cache
    if http_cache.exists():
        try:
            http_cache.unlink()
            cleared.append('HTTP cache')
        except Exception as e:
            bsn_logger.error(f'HTTP cache clear failed: {e}')
            raise RuntimeError(f"Error clearing HTTP cache: {e}")

    return cleared


# Tables (and id column) that can hold a stale song reference
_SONG_REF_TABLES = (
    ('bookmarks', 'song_id'),
    ('likes', 'item_id'),
    ('ratings', 'item_id'),
    ('play_stats', 'song_id'),
    ('play_queue', 'current'),
    ('play_queue_entries', 'song_id'),
    ('share_entries', 'item_id'),
)


@with_app_context
def sweep_stale_references() -> dict[str, int]:
    """
    Finds and deletes rows left behind by deleted content.
    Returns the number of rows purged, keyed by a short description.
    """
    from beetsplug.beetstreamnext.api.idmapper import IDMapper

    purged: dict[str, int] = {}

    with database() as db:
        # Podcast episodes: only bookmarks can reference one
        stale_pe = [
            row[0] for row in db.execute(
                """
                SELECT b.song_id
                FROM bookmarks b
                LEFT JOIN podcast_episodes pe ON pe.id = CAST(substr(b.song_id, 4) AS INTEGER)
                WHERE b.song_id LIKE 'pe-%' AND pe.id IS NULL
                """
            ).fetchall()
        ]
        if stale_pe:
            placeholders = ','.join('?' * len(stale_pe))
            db.execute(
                f"""
                DELETE FROM bookmarks 
                WHERE song_id IN ({placeholders})
                """, stale_pe
            )
            purged['bookmarks (deleted podcast episodes)'] = len(stale_pe)

        # Songs

        refs_by_table: dict[tuple[str, str], list[str]] = {}
        all_refs: set[str] = set()

        for table, column in _SONG_REF_TABLES:
            rows = db.execute(
                f"""SELECT DISTINCT {column} 
                FROM {table} 
                WHERE {column} 
                LIKE 'sg-%'
                """
            ).fetchall()

            ids = [row[0] for row in rows]
            refs_by_table[(table, column)] = ids
            all_refs.update(ids)

        if all_refs:
            resolved = IDMapper.resolve_songs_bulk(list(all_refs))
            stale_songs = all_refs - resolved.keys()

            for (table, column), ids in refs_by_table.items():

                to_delete = [i for i in ids if i in stale_songs]
                if not to_delete:
                    continue

                placeholders = ','.join('?' * len(to_delete))
                db.execute(
                    f"""
                    DELETE FROM {table} 
                    WHERE {column} 
                    IN ({placeholders})
                    """, to_delete
                )

                purged[f'{table} (deleted songs)'] = len(to_delete)

    return purged


def run_periodic():
    """
    Runs housekeeping periodically.
    Deletes old cached thumbnails, purges rate limiting store.
    """

    global _last_cleanup

    now = time.time()
    if now - _last_cleanup < CLEANUP_INTERVAL_SEC:
        return

    if not _cleanup_lock.acquire(blocking=False):
        return  # another thread already doing it

    try:
        if now - _last_cleanup < CLEANUP_INTERVAL_SEC:
            return
        _last_cleanup = now
    finally:
        _cleanup_lock.release()

    def _background_maintenance():
        bsn_logger.info(f"[{datetime.fromtimestamp(now)}] Starting background maintenance...")

        rate_limiter.sweep()

        # Poll subscribed podcast feeds for new episodes
        try:
            app.config['podcast_manager'].refresh()
        except Exception as e:
            bsn_logger.error(f'Podcast feed refresh failed: {e}')

        # Purge stale refs
        try:
            purged = sweep_stale_references()
            if purged:
                details = ', '.join(f'{n} {label}' for label, n in purged.items())
                bsn_logger.info(f'Database sanity sweep purged: {details}')
        except Exception as e:
            bsn_logger.error(f'Database sanity sweep failed: {e}')

        # Tidy cache
        cache_dir = app.config['THUMBNAIL_CACHE_PATH']
        if cache_dir.exists():
            max_age_seconds = MAX_CACHE_AGE_DAYS * 86400
            try:
                for f in cache_dir.iterdir():
                    if f.suffix == '.jpg' and (now - f.stat().st_mtime > max_age_seconds):
                        f.unlink(missing_ok=True)
            except Exception as e:
                bsn_logger.error(f"Error cleaning thumbnail cache: {e}")

        bsn_logger.info(f"[{datetime.fromtimestamp(now)}] Background maintenance complete.")

    thread = threading.Thread(target=_background_maintenance, daemon=True)
    thread.start()