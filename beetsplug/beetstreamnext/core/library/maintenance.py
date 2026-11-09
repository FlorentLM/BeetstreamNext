from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from beetsplug.beetstreamnext.constants import (
    CACHE_LOCATION, CLEANUP_INTERVAL_SEC, SOURCE_TRACK_ID_FIELDS, SOURCE_ALBUM_ID_FIELDS, SCROBBLE_RETRY_INTERVAL_SEC, MAX_CACHE_AGE_DAYS, SERVER_NAME,
    TRANSCODE_TMP_DIR, HLS_CACHE_DIR, ZIP_CACHE_DIR,
    TRANSCODE_MAX_AGE_SEC, HLS_MAX_AGE_SEC, ZIP_MAX_AGE_SEC,
)
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.core.storage.connection import database, dual_database
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.library.health import scan_library
from beetsplug.beetstreamnext.core.accounts.security import rate_limiter
from beetsplug.beetstreamnext.core.accounts.sessions import purge_expired_sessions
from beetsplug.beetstreamnext.core.config.settings_schema import SETTINGS_SCHEMA
from beetsplug.beetstreamnext.utils.system import purge, dir_size
from beetsplug.beetstreamnext.utils.db import chunked_execute, library_cache
from beetsplug.beetstreamnext.utils.text import validate_mbid
from beetsplug.beetstreamnext.utils.concurrency import Throttle

_cleanup_throttle = Throttle()
scrobble_retry_throttle = Throttle()


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


def clear_requests_caches(thumb_dir: str | Path, http_cache: str | Path) -> list[str]:
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
            raise RuntimeError(f'Error clearing thumbnail cache: {e}') from e

    # HTTP cache
    if http_cache.exists():
        try:
            http_cache.unlink()
            cleared.append('HTTP cache')
        except Exception as e:
            bsn_logger.error(f'HTTP cache clear failed: {e}')
            raise RuntimeError(f"Error clearing HTTP cache: {e}") from e

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


_SWEEP_MIN_REFS = 20
_SWEEP_MAX_STALE_RATIO = 0.5


@with_app_context
def remap_source_ids() -> int:
    """
    Rewrites all references (from IDs minted when mb_* fields were Spotify/Deezer IDs) to the current IDs.
    Artists also move from name-based IDs to MusicBrainz IDs.

    Returns rewritten or dropped count.
    """

    id_map: dict[str, str] = {}
    changed = 0

    with dual_database() as db:

        # Tracks
        track_rows = db.execute(
            f"""
            SELECT i.id, i.mb_trackid, i.mb_releasetrackid, i.path, a.value
            FROM beets.item_attributes a
            JOIN beets.items i ON i.id = a.entity_id
            WHERE a.key IN ({','.join('?' * len(SOURCE_TRACK_ID_FIELDS))})
            """, SOURCE_TRACK_ID_FIELDS  # nosec B608  # noqa: S608 vars are constants
        ).fetchall()

        for trow in track_rows:
            new = IDs.encode_song({'id': trow[0], 'mb_trackid': trow[1], 'mb_releasetrackid': trow[2], 'path': trow[3]})
            old = IDs.encode_song({'mb_trackid': str(trow[4])})
            if old != new:
                id_map[old] = new

        # Albums
        album_rows = db.execute(
            f"""
            SELECT DISTINCT al.id, al.mb_albumid, al.albumartist, al.album, a.value
            FROM beets.item_attributes a
            JOIN beets.items i ON i.id = a.entity_id
            JOIN beets.albums al ON al.id = i.album_id
            WHERE a.key IN ({','.join('?' * len(SOURCE_ALBUM_ID_FIELDS))})
            """, SOURCE_ALBUM_ID_FIELDS  # nosec B608  # noqa: S608 vars are constants
        ).fetchall()

        for arow in album_rows:
            new = IDs.encode_album(arow[0], arow[1], arow[2], arow[3])
            old = IDs.encode_album(None, str(arow[4]))
            if old != new:
                id_map[old] = new

        # Artists
        item_cols, album_cols = library_cache.schema('items'), library_cache.schema('albums')
        artist_queries = (
            (
                f"""
                SELECT DISTINCT al.albumartist, al.mb_albumartistid, {'al.albumartists' if 'albumartists' in album_cols else 'NULL'}
                FROM beets.item_attributes a
                JOIN beets.items i ON i.id = a.entity_id
                JOIN beets.albums al ON al.id = i.album_id
                WHERE a.key IN ({','.join('?' * len(SOURCE_ALBUM_ID_FIELDS))})
                """, SOURCE_ALBUM_ID_FIELDS
            ), (
                f"""
                SELECT DISTINCT i.artist, i.mb_artistid, {'i.artists' if 'artists' in item_cols else 'NULL'}
                FROM beets.item_attributes a
                JOIN beets.items i ON i.id = a.entity_id
                WHERE a.key IN ({','.join('?' * len(SOURCE_TRACK_ID_FIELDS))})
                """, SOURCE_TRACK_ID_FIELDS
            )
        )  # nosec B608  # noqa: S608 vars are constants

        for query, params in artist_queries:

            for name, mbid, multi in db.execute(query, params).fetchall():
                mbid = validate_mbid(mbid)

                if name and mbid and not IDs.is_joint_credit(multi, name):
                    id_map[IDs.encode_artist(name, is_mbid=False)] = IDs.encode_artist(mbid, is_mbid=True)

        for old, new in id_map.items():

            for table, column in _SONG_REF_TABLES:
                # "OR IGNORE" to keep existing row for new ID, drop old one
                changed += db.execute(
                    f"""
                    UPDATE OR IGNORE {table}
                    SET {column} = ?
                    WHERE {column} = ?
                    """, (new, old)  # nosec B608  # noqa: S608 vars are constants
                ).rowcount
                changed += db.execute(
                    f"""
                    DELETE FROM {table}
                    WHERE {column} = ?
                    """, (old,)  # nosec B608  # noqa: S608 vars are constants
                ).rowcount

    return changed


@with_app_context
def sweep_stale_references() -> dict[str, int]:
    """
    Finds and deletes rows left behind by deleted content.
    Returns the number of rows purged, keyed by a short description.
    """
    from beetsplug.beetstreamnext.core.library.resolve import Resolve

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
            chunked_execute(
                db,
                """
                DELETE FROM bookmarks 
                WHERE song_id IN ({q})
                """, stale_pe
            )
            purged['bookmarks (deleted podcast episodes)'] = len(stale_pe)

        # Songs

        refs_by_table: dict[tuple[str, str], list[str]] = {}
        all_refs: set[str] = set()

        for table, column in _SONG_REF_TABLES:
            rows = db.execute(
                f"""
                SELECT DISTINCT {column} 
                FROM {table} 
                WHERE {column} 
                LIKE 'sg-%'
                """  # nosec B608  # noqa: S608 vars are constants
            ).fetchall()

            ids = [row[0] for row in rows]
            refs_by_table[(table, column)] = ids
            all_refs.update(ids)

        if all_refs:
            resolved = Resolve.songs(list(all_refs))
            stale_songs = all_refs - resolved.keys()

            # A library that was swapped, emptied or failed to load would otherwise wipe everyone's data
            if len(all_refs) >= _SWEEP_MIN_REFS and len(stale_songs) / len(all_refs) > _SWEEP_MAX_STALE_RATIO:
                bsn_logger.warning(
                    f'{len(stale_songs)}/{len(all_refs)} song references seem stale, which is sus. Is the right beets library loaded?'
                )
                stale_songs = set()

            for (table, column), ids in refs_by_table.items():

                to_delete = [i for i in ids if i in stale_songs]
                if not to_delete:
                    continue

                chunked_execute(
                    db,
                    f"""
                    DELETE FROM {table} 
                    WHERE {column} 
                    IN ({{q}})
                    """, to_delete  # nosec B608  # noqa: S608 vars are constants
                )

                purged[f'{table} (deleted songs)'] = len(to_delete)

        # Images of artists that left the library
        from beetsplug.beetstreamnext.core.media.artist_images import cleanup_artist_records
        n = cleanup_artist_records()
        if n:
            purged['artist image(s) (artists no longer in library)'] = n

        # Settings: keys no longer present in the schema (renamed/removed settings)
        placeholders = ','.join('?' * len(SETTINGS_SCHEMA))
        cur = db.execute(
            f"""
            DELETE FROM settings
            WHERE key NOT IN ({placeholders})
            """, tuple(SETTINGS_SCHEMA.keys())  # nosec B608  # noqa: S608 vars are constants
        )
        if cur.rowcount:
            purged['settings (unknown keys)'] = cur.rowcount

    return purged


def run_periodic():
    """
    Runs housekeeping periodically.
    Deletes old cached thumbnails, purges rate limiting store.
    """

    if scrobble_retry_throttle.allow(SCROBBLE_RETRY_INTERVAL_SEC):
        from beetsplug.beetstreamnext.core.services.scrobbling.service import deliver_queued, workers
        workers.submit(deliver_queued)      # retries listens that failed earlier

    if not _cleanup_throttle.allow(CLEANUP_INTERVAL_SEC):
        return

    now = time.time()

    def _background_maintenance():
        bsn_logger.info(f"[{datetime.fromtimestamp(now)}] Starting background maintenance...")

        rate_limiter.sweep()

        try:
            purge_expired_sessions()
        except Exception as e:
            bsn_logger.error(f'Expired web session cleanup failed: {e}')

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

        # Follow IDs replaced by a MusicBrainz re-import
        try:
            remapped = remap_source_ids()
            if remapped:
                bsn_logger.info(f'{SERVER_NAME} remapped {remapped} stored reference(s) to IDs that changed source')

        except Exception as e:
            bsn_logger.error(f'{SERVER_NAME} ID remapping failed: {e}')

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
            if counts['flagged'] or counts['checked'] or counts['pruned']:
                bsn_logger.info(
                    f"{SERVER_NAME} health scan: {counts['checked']} checked, "
                    f"{counts['flagged']} flagged, {counts['skipped']} unchanged (skipped), "
                    f"{counts['pruned']} stale entries pruned."
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