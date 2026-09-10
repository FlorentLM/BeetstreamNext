import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List

from beetsplug.beetstreamnext.constants import (
    CACHE_LOCATION, CLEANUP_INTERVAL_SEC, MAX_CACHE_AGE_DAYS, SERVER_NAME,
    TRANSCODE_TMP_DIR, HLS_CACHE_DIR, ZIP_CACHE_DIR,
    TRANSCODE_MAX_AGE_SEC, HLS_MAX_AGE_SEC, ZIP_MAX_AGE_SEC,
)
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.health import scan_library
from beetsplug.beetstreamnext.core.security import rate_limiter
from beetsplug.beetstreamnext.schemas import SETTINGS_SCHEMA
from beetsplug.beetstreamnext.utils.system import purge, dir_size

_cleanup_lock = threading.Lock()
_last_cleanup: float = 0.0


def cache_breakdown(thumb_dir: str | Path, http_cache: str | Path) -> dict[str, int]:
    """Size (in bytes) of each disk cache/tmp category."""
    http_cache = Path(http_cache)

    breakdown = {
        'Image thumbnails': dir_size(Path(thumb_dir)),
        'HTTP cache': http_cache.stat().st_size if http_cache.exists() else 0,
        'Transcode tempfiles': dir_size(TRANSCODE_TMP_DIR),
        'HLS sessions': dir_size(HLS_CACHE_DIR),
        'Zip downloads': dir_size(ZIP_CACHE_DIR),
        'Podcast downloads': dir_size(app.config['podcast_manager'].storage_dir()),
    }

    other = dir_size(CACHE_LOCATION) - sum(breakdown.values())
    if other > 0:
        breakdown['Other'] = other

    return breakdown


def clear_requests_caches(thumb_dir: str | Path, http_cache: str | Path) -> List[str]:
    """Clears thumbnails and HTTP cache. Returns a list of what was cleared."""
    cleared = []

    thumb_dir = Path(thumb_dir)
    http_cache = Path(http_cache)

    # Thumbnails
    if thumb_dir.exists():
        try:
            n = purge(thumb_dir, suffix='.jpg')
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


def clear_tmp_files() -> dict[str, int]:
    """Removes leaked transcode tempfiles, abandoned HLS sessions and old zip downloads."""
    now = time.time()
    purged: dict[str, int] = {}

    n = purge(TRANSCODE_TMP_DIR, TRANSCODE_MAX_AGE_SEC, now)
    if n:
        purged['transcode tempfile(s)'] = n

    n = purge(HLS_CACHE_DIR, HLS_MAX_AGE_SEC, now)
    if n:
        purged['abandoned HLS session(s)'] = n

    n = purge(ZIP_CACHE_DIR, ZIP_MAX_AGE_SEC, now)
    if n:
        purged['old zip download(s)'] = n

    return purged


def clear_offline_files() -> dict[str, int]:
    """
    Remove all old/unneeded offline files (tmp, hls, zips and leftover podcast files).
    """
    purge_report = clear_tmp_files()

    try:
        pm = app.config['podcast_manager']
        rep = pm.remove_leftovers()
        purge_report.update(rep)

    except Exception as e:
        bsn_logger.error(f'Leftover podcasts cleanup failed: {e}')

    return purge_report


# Tables (and id column) that can hold a stale song reference
_SONG_REF_TABLES = (
    ('bookmarks', 'song_id'),
    ('likes', 'item_id'),
    ('ratings', 'item_id'),
    ('play_stats', 'song_id'),
    ('play_queue', 'current'),
    ('play_queue_entries', 'song_id'),
    ('share_entries', 'item_id'),
    ('song_checks', 'song_id'),
)


@with_app_context
def sweep_stale_references() -> dict[str, int]:
    """
    Finds and deletes rows left behind by deleted content.
    Returns the number of rows purged, keyed by a short description.
    """
    from beetsplug.beetstreamnext.core.mappings import Resolve

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
            resolved = Resolve.songs(list(all_refs))
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

        # Settings: keys no longer present in the schema (renamed/removed settings)
        placeholders = ','.join('?' * len(SETTINGS_SCHEMA))
        cur = db.execute(
            f"""
            DELETE FROM settings
            WHERE key NOT IN ({placeholders})
            """, tuple(SETTINGS_SCHEMA.keys())
        )
        if cur.rowcount:
            purged['settings (unknown keys)'] = cur.rowcount

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

        # Recover channel/episode still on 'downloading' (e.g. server shutdown mid-download)
        try:
            app.config['podcast_manager'].resume_downloads()
        except Exception as e:
            bsn_logger.error(f'Podcast episode download recovery failed: {e}')

        # Purge stale refs
        try:
            purged = sweep_stale_references()
            if purged:
                details = ', '.join(f'{n} {label}' for label, n in purged.items())
                bsn_logger.info(f'{SERVER_NAME} database cleanup purged: {details}')
        except Exception as e:
            bsn_logger.error(f'{SERVER_NAME} database cleanup failed: {e}')

        # Incremental audio health scan
        try:
            counts = scan_library(full=False)
            if counts['flagged'] or counts['checked']:
                bsn_logger.info(
                    f"{SERVER_NAME} health scan: {counts['checked']} checked, "
                    f"{counts['flagged']} flagged, {counts['skipped']} unchanged (skipped)."
                )
        except Exception as e:
            bsn_logger.error(f'{SERVER_NAME} health scan failed: {e}')

        # Tidy cache
        try:
            purge(app.config['THUMBNAIL_CACHE_PATH'], MAX_CACHE_AGE_DAYS * 86400, now, suffix='.jpg')
        except Exception as e:
            bsn_logger.error(f"Error cleaning thumbnail cache: {e}")

        # Delete old tmp/session files and leftover podcast files
        try:
            purged = clear_offline_files()
            if purged:
                details = ', '.join(f'{n} {label}' for label, n in purged.items())
                bsn_logger.info(f'{SERVER_NAME} cache cleanup purged: {details}')
        except Exception as e:
            bsn_logger.error(f'{SERVER_NAME} cache cleanup failed: {e}')

        bsn_logger.info(f"[{datetime.fromtimestamp(now)}] Background maintenance complete.")

    thread = threading.Thread(target=_background_maintenance, daemon=True)
    thread.start()