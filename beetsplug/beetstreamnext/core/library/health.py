from __future__ import annotations

import os
import platform
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import NamedTuple

import beets
from beets.library import Album, Item, Library as BeetsLibrary

from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.core.storage.connection import database, dual_database
from beetsplug.beetstreamnext.core.services.events import admin_events
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.notifications import notify
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.constants import FFMPEG_INPUT_ARGS, START_TIME, SERVER_VERSION
from beetsplug.beetstreamnext.core.library.beets.interaction import config_path
from beetsplug.beetstreamnext.utils.system import find_binary, binary_version, resolve_path
from beetsplug.beetstreamnext.utils.general import subprocess_env
from beetsplug.beetstreamnext.utils.text import format_duration, format_elapsed
from beetsplug.beetstreamnext.utils.db import chunked_execute
from beetsplug.beetstreamnext.utils.concurrency import SingleAccess, Throttle


class Check(NamedTuple):
    label: str
    badge: str
    sql: str = ''


CHECKS = {

    'decode_errors': Check('Decode errors', 'badge-red'),
    'missing_file': Check('Missing file', 'badge-rose'),

    'quality_duplicate': Check('Duplicate', 'badge-blue', """
        WITH k AS (
            SELECT id,
                   CASE WHEN TRIM(COALESCE(mb_trackid, '')) != '' THEN mb_trackid
                        ELSE LOWER(TRIM(artist)) || '|' || LOWER(TRIM(title)) || '|' || CAST(COALESCE(length, 0) / 3 AS INT)
                   END AS key
            FROM beets.items
            WHERE TRIM(COALESCE(title, '')) != ''
        ), g AS (
            SELECT id, COUNT(*) OVER (PARTITION BY key) AS copies FROM k
        )
        SELECT id, copies || ' copies of this track (same ID, or same artist, title and length)'
        FROM g WHERE copies > 1
    """),

    'quality_same_recording': Check('Same recording', 'badge-indigo', """
        WITH fp AS (
            SELECT id, mb_trackid, bitrate,
                   COALESCE(NULLIF(TRIM(acoustid_id), ''), 'fp:' || TRIM(acoustid_fingerprint)) AS key,
                   COALESCE(NULLIF(format, ''), '?')
                   || CASE WHEN COALESCE(bitrate, 0) > 0 THEN ' ' || (bitrate / 1000) || ' kbps' ELSE '' END AS quality
            FROM beets.items
            WHERE TRIM(COALESCE(acoustid_id, '')) != '' OR TRIM(COALESCE(acoustid_fingerprint, '')) != ''
        ), g AS (
            SELECT key, COUNT(*) AS copies, GROUP_CONCAT(quality, ', ') AS qualities
            FROM (SELECT * FROM fp ORDER BY bitrate DESC)
            GROUP BY key
            HAVING COUNT(*) > 1 AND COUNT(DISTINCT COALESCE(NULLIF(TRIM(mb_trackid), ''), id)) > 1
        )
        SELECT fp.id, g.copies || ' copies of this recording by fingerprint (' || g.qualities || '), this one is '
                      || fp.quality
        FROM fp JOIN g ON g.key = fp.key
    """),

    'quality_lossy_copy': Check('Lossy copy', 'badge-amber', """
        WITH a AS (
            SELECT al.id AS album_id, MIN(i.id) AS rep, COUNT(*) AS n,
                   SUM(UPPER(i.format) IN ('FLAC', 'ALAC', 'WAV', 'AIFF', 'APE', 'WAVPACK', 'WV', 'DSF', 'DSDIFF', 'TTA')) AS lossless,
                   MIN(i.format) AS format,
                   LOWER(TRIM(al.albumartist)) || '|' || LOWER(TRIM(al.album)) AS key
            FROM beets.albums al JOIN beets.items i ON i.album_id = al.id
            GROUP BY al.id
        )
        SELECT a.rep, 'Lossy (' || a.format || ') copy, a lossless version of this album is also in the library'
        FROM a
        WHERE a.lossless = 0 AND EXISTS (
            SELECT 1 FROM a b WHERE b.key = a.key AND b.album_id != a.album_id AND b.lossless = b.n
        )
    """),

    'quality_inconsistent': Check('Inconsistent tags', 'badge-violet', """
        SELECT MIN(id), 'Tracks of this album disagree on the year (' || GROUP_CONCAT(DISTINCT NULLIF(year, 0)) || ')'
        FROM beets.items
        WHERE album_id IS NOT NULL
        GROUP BY album_id
        HAVING COUNT(DISTINCT NULLIF(year, 0)) > 1
        UNION ALL
        SELECT MIN(id), 'Tracks of disc ' || disc || ' disagree on the track total ('
                        || GROUP_CONCAT(DISTINCT NULLIF(tracktotal, 0)) || ')'
        FROM beets.items
        WHERE album_id IS NOT NULL
        GROUP BY album_id, disc
        HAVING COUNT(DISTINCT NULLIF(tracktotal, 0)) > 1
        UNION ALL
        SELECT MIN(id), 'Tracks of this album disagree on the album title (' || COUNT(DISTINCT album) || ' variants)'
        FROM beets.items
        WHERE album_id IS NOT NULL
        GROUP BY album_id
        HAVING COUNT(DISTINCT album) > 1
        UNION ALL
        SELECT MIN(id), 'Tracks of this album disagree on the album artist (' || COUNT(DISTINCT albumartist) || ' variants)'
        FROM beets.items
        WHERE album_id IS NOT NULL
        GROUP BY album_id
        HAVING COUNT(DISTINCT albumartist) > 1
    """),

    'quality_missing_tracks': Check('Missing tracks', 'badge-cyan', """
        SELECT MIN(id), 'Disc ' || disc || ' is missing ' || (MAX(track) - MIN(track) + 1 - COUNT(DISTINCT track))
                        || ' track(s) between ' || MIN(track) || ' and ' || MAX(track)
        FROM beets.items
        WHERE album_id IS NOT NULL AND track > 0
        GROUP BY album_id, disc
        HAVING MAX(track) - MIN(track) + 1 > COUNT(DISTINCT track)
    """),

    'quality_mixed_release': Check('Mixed releases', 'badge-orange', """
        SELECT MIN(id),
               'Tracks of this album come from ' || COUNT(DISTINCT NULLIF(TRIM(mb_albumid), '')) || ' different releases'
               || CASE COUNT(DISTINCT NULLIF(TRIM(mb_releasegroupid), ''))
                      WHEN 0 THEN ''
                      WHEN 1 THEN ' (same release group)'
                      ELSE ' (' || COUNT(DISTINCT NULLIF(TRIM(mb_releasegroupid), '')) || ' release groups)'
                  END
        FROM beets.items
        WHERE album_id IS NOT NULL
        GROUP BY album_id
        HAVING COUNT(DISTINCT NULLIF(TRIM(mb_albumid), '')) > 1
    """),

    'quality_artist_mismatch': Check('Artist mismatch', 'badge-lime', """
        SELECT MIN(id), 'None of the track artist IDs is the album artist ID'
        FROM beets.items
        WHERE album_id IS NOT NULL
          AND TRIM(COALESCE(mb_albumartistid, '')) != ''
          AND mb_albumartistid != '89ad4ac3-39f7-470e-963a-56509c546377'
          AND COALESCE(comp, 0) = 0
          AND LOWER(TRIM(albumartist)) NOT IN ('various artists', 'various')
        GROUP BY album_id
        HAVING SUM(TRIM(COALESCE(mb_artistid, '')) != '') > 0
           AND SUM(mb_artistid = mb_albumartistid OR INSTR(COALESCE(mb_artistids, ''), mb_albumartistid) > 0) = 0
    """),

    'quality_missing_tags': Check('Missing tags', 'badge-fuchsia', """
        SELECT id, 'No ' || RTRIM(missing, ', ')
        FROM (
            SELECT id,
                   CASE WHEN TRIM(COALESCE(title, '')) = '' THEN 'title, ' ELSE '' END
                   || CASE WHEN TRIM(COALESCE(artist, '')) = '' THEN 'artist, ' ELSE '' END
                   || CASE WHEN album_id IS NOT NULL AND TRIM(COALESCE(albumartist, '')) = '' THEN 'album artist, ' ELSE '' END
                   || CASE WHEN album_id IS NOT NULL AND TRIM(COALESCE(album, '')) = '' THEN 'album, ' ELSE '' END
                   || CASE WHEN album_id IS NOT NULL AND COALESCE(track, 0) <= 0 THEN 'track number, ' ELSE '' END
                   AS missing
            FROM beets.items
        )
        WHERE missing != ''
    """),

    'quality_no_id': Check('No ID', '', """
        SELECT id, 'No MusicBrainz recording ID'
        FROM beets.items
        WHERE TRIM(COALESCE(mb_trackid, '')) = '' AND TRIM(COALESCE(mb_releasetrackid, '')) = ''
    """),

    'quality_no_fingerprint': Check('No fingerprint', '', """
        SELECT id, 'Not checked for same-recording copies: no AcoustID or fingerprint'
        FROM beets.items
        WHERE TRIM(COALESCE(acoustid_id, '')) = '' AND TRIM(COALESCE(acoustid_fingerprint, '')) = ''
          AND EXISTS (
              SELECT 1 FROM beets.items
              WHERE TRIM(COALESCE(acoustid_id, '')) != '' OR TRIM(COALESCE(acoustid_fingerprint, '')) != ''
          )
    """),

}

INTEGRITY_CATEGORIES = tuple(cat for cat, check in CHECKS.items() if not check.sql)

MAX_QUAL_REPORTS = 500   # max flagged items per quality type
MAX_ERRORS = 10   # max error timestamps shown per track (in details column)


health_scan = SingleAccess()
status_push = Throttle()


_DTS_WARNING_RE = re.compile(r'non monotonically increasing dts to muxer in stream \d+: (\d+) >=')

def _parse_error_positions(stderr: str, samplerate: int) -> list[str]:

    if not samplerate:
        return []

    positions = []
    last_sample = None
    for match in _DTS_WARNING_RE.finditer(stderr):
        sample = int(match.group(1))
        if sample != last_sample:
            positions.append(format_duration(sample / samplerate))
            last_sample = sample

    return positions


def check_decode_errors(file_path: str | Path, samplerate: int = 0) -> tuple[bool, str]:
    """
    Decode a file's audio stream through and looks for ffmpeg-reported corruption.
    """
    ffmpeg_bin = find_binary('ffmpeg') or 'ffmpeg'

    try:
        result = subprocess.run(  # noqa: S603 argument list, no shell  # nosec B603 (argument list, no shell)
            [ffmpeg_bin, '-v', 'error', *FFMPEG_INPUT_ARGS, '-i', str(file_path), '-map', '0:a', '-f', 'null', '-'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300, env=subprocess_env()
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f'probe failed: {e}'

    stderr = result.stderr.decode('utf-8', errors='replace')
    error_count = stderr.count('Decoding error:')

    if not error_count:
        if result.returncode == 0:
            return True, ''

        # ffmpeg can't open or map the file (corrupt header, no audio stream...)
        last_line = next((line.strip() for line in reversed(stderr.splitlines()) if line.strip()), '')
        detail = f'ffmpeg failed (exit {result.returncode})'
        return False, f'{detail}: {last_line[:200]}' if last_line else detail

    detail = f'{error_count} decode error{"s" if error_count != 1 else ""}'

    positions = _parse_error_positions(stderr, samplerate)

    if positions:
        shown = positions[:MAX_ERRORS]
        more = len(positions) - len(shown)
        detail += f" (near {', '.join(shown)})"
        if more:
            detail += f' (+{more} more)'

    return False, detail


def needs_healing(song_id: str) -> bool:
    """True if a song is flagged with an unresolved file integrity issue (quality findings don't count)."""

    with database() as db:
        row = db.execute(
            """
            SELECT 1 
            FROM song_checks 
            WHERE song_id = ? AND ok = 0 AND kind IN (?, ?)
            LIMIT 1
            """, (song_id, *INTEGRITY_CATEGORIES)
        ).fetchone()

    return row is not None


def health_stats() -> dict[str, int]:
    """Returns total songs checked, how many have a file integrity issue, and how many quality findings there are."""

    with database() as db:
        row = db.execute(
            """
            SELECT COALESCE(SUM(kind IN (?, ?)), 0) AS total,
                   COALESCE(SUM(ok = 0 AND kind IN (?, ?)), 0) AS flagged,
                   COALESCE(SUM(ok = 0 AND kind NOT IN (?, ?)), 0) AS quality
            FROM song_checks
            """, INTEGRITY_CATEGORIES * 3
        ).fetchone()

    return {'total': row['total'], 'flagged': row['flagged'], 'quality': row['quality']}


@with_app_context
def flagged_songs() -> list[dict]:
    """
    Resolves currently flagged songs, with display info (most recently checked first)
    """

    from beetsplug.beetstreamnext.core.library.resolve import Resolve

    with database() as db:
        rows = db.execute(
            """
            SELECT song_id, kind, detail, checked_at
            FROM song_checks
            WHERE ok = 0
            ORDER BY checked_at DESC
            """
        ).fetchall()

    if not rows:
        return []

    resolved = Resolve.songs([row['song_id'] for row in rows])

    result = []
    for row in rows:
        item = resolved.get(row['song_id'])
        check = CHECKS.get(row['kind']) or Check(row['kind'], '')

        result.append({
            'song_id': row['song_id'],
            'album_id': item.get('album_id') if item else None,
            'missing': item is None,
            'title': item.get('title') if item else '(deleted)',
            'artist': item.get('artist') if item else '',
            'album': item.get('album') if item else '',
            'kind': row['kind'],
            'label': check.label,
            'badge': check.badge,
            'detail': row['detail'],
            'checked_at': row['checked_at'],
        })

    return result


def scan_errors(db: sqlite3.Connection, items: list[sqlite3.Row], song_ids: dict[int, str], full: bool) -> dict[str, int]:
    """
    Check for missing files and decode errors, incrementally
    (and prune the results of songs that aren't in the library anymore)
    """

    counts = {'checked': 0, 'flagged': 0, 'skipped': 0, 'pruned': 0}
    music_root = app.config['music_root']

    can_decode = bool(find_binary('ffmpeg'))
    if not can_decode:
        bsn_logger.warning('Health scan: ffmpeg not found, skipping decode checks.')

    existing: dict[str, dict[str, tuple[float, int]]] = {}
    for row in db.execute(
        """
        SELECT song_id, kind, mtime, ok
        FROM song_checks
        WHERE kind IN (?, ?)
        """, INTEGRITY_CATEGORIES
    ).fetchall():
        existing.setdefault(row['song_id'], {})[row['kind']] = (row['mtime'], row['ok'])

    seen_song_ids: set[str] = set()

    for item in items:
        if status_push.allow(1.0):
            admin_events.publish('health-scan', '')
        path = os.fsdecode(item['path'] or b'')
        if not path:
            continue

        song_id = song_ids[item['id']]
        seen_song_ids.add(song_id)

        path_obj = resolve_path(path, music_root)
        prev_missing = None if full else existing.get(song_id, {}).get('missing_file')

        try:
            mtime = os.path.getmtime(path_obj)
        except OSError:
            if prev_missing is not None and prev_missing[1] == 0:
                counts['skipped'] += 1
                continue   # already flagged missing, nothing new to record

            counts['checked'] += 1
            counts['flagged'] += 1
            bsn_logger.warning(f"Health check flagged missing file: '{path_obj}'")

            db.execute(
                """
                INSERT INTO song_checks (song_id, kind, mtime, ok, detail)
                VALUES (?, ?, 0, 0, 'File not found on disk')
                ON CONFLICT (song_id, kind) DO UPDATE SET
                    mtime = 0, ok = 0,
                    detail = 'File not found on disk', checked_at = unixepoch()
                """, (song_id, 'missing_file')
            )
            db.commit()
            continue

        if prev_missing is not None and prev_missing[1] == 0:
            # File reappeared since last flagged missing: clear flag
            db.execute(
                """
                INSERT INTO song_checks (song_id, kind, mtime, ok, detail)
                VALUES (?, ?, ?, 1, '')
                ON CONFLICT (song_id, kind) DO UPDATE SET
                    mtime = excluded.mtime, ok = 1,
                    detail = '', checked_at = unixepoch()
                """, (song_id, 'missing_file', mtime)
            )
            db.commit()
            bsn_logger.info(f"Health check: '{path_obj}' reappeared, cleared missing-file flag.")

        if not can_decode:
            continue

        prev = None if full else existing.get(song_id, {}).get('decode_errors')
        if prev is not None and prev[0] == mtime:
            counts['skipped'] += 1
            continue

        ok, detail = check_decode_errors(path_obj, samplerate=item['samplerate'] or 0)
        counts['checked'] += 1
        if not ok:
            counts['flagged'] += 1
            bsn_logger.warning(f"Health check flagged '{path_obj.name}': {detail}")

        db.execute(
            """
            INSERT INTO song_checks (song_id, kind, mtime, ok, detail)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (song_id, kind) DO UPDATE SET
                mtime = excluded.mtime, ok = excluded.ok,
                detail = excluded.detail, checked_at = unixepoch()
            """, (song_id, 'decode_errors', mtime, int(ok), detail)
        )
        db.commit()

    stale_ids = existing.keys() - seen_song_ids
    if stale_ids:
        chunked_execute(
            db,
            """
            DELETE FROM song_checks
            WHERE song_id IN ({q}) AND kind IN (?, ?)
            """, list(stale_ids), list(INTEGRITY_CATEGORIES)
        )
        db.commit()
        counts['pruned'] = len(stale_ids)

    return counts


def scan_quality(db: sqlite3.Connection, song_ids: dict[int, str]) -> int:
    """
    Runs quality check and update any previous findings.
    """

    findings = []
    for kind, check in CHECKS.items():
        if not check.sql:
            continue

        rows = db.execute(check.sql).fetchall()
        if len(rows) > MAX_QUAL_REPORTS:
            bsn_logger.warning(f"Quality check '{kind}': {len(rows)} findings, only the first "
                               f"{MAX_QUAL_REPORTS} are listed.")

        findings += [(song_ids[row[0]], kind, row[1]) for row in rows[:MAX_QUAL_REPORTS] if row[0] in song_ids]

    db.execute(
        """
        DELETE FROM song_checks WHERE kind LIKE 'quality\\_%' ESCAPE '\\'
        """
    )
    db.executemany(
        """
        INSERT INTO song_checks (song_id, kind, mtime, ok, detail)
        VALUES (?, ?, 0, 0, ?)
        ON CONFLICT (song_id, kind) DO UPDATE SET detail = detail || '; ' || excluded.detail, checked_at = unixepoch()
        """, findings
    )

    return len(findings)


def _failing_checks(db: sqlite3.Connection) -> set[tuple[str, str]]:
    """(track_id, kind) of every failing check"""
    return {(r['song_id'], r['kind']) for r in db.execute("SELECT song_id, kind FROM song_checks WHERE ok = 0")}


@with_app_context
def scan_library(full: bool = False) -> dict[str, int]:
    """
    Run the file checks (decode errors, missing files) incrementally, then refresh the quality findings.
    """

    counts = {'checked': 0, 'flagged': 0, 'skipped': 0, 'pruned': 0, 'quality': 0}

    if not health_scan.try_acquire():
        bsn_logger.info('Health scan already running.')
        return counts

    admin_events.publish('health-scan', '')
    music_root = app.config['music_root']

    try:
        with dual_database() as db:
            items = db.execute(
                """
                SELECT id, mb_trackid, mb_releasetrackid, path, samplerate FROM beets.items
                """
            ).fetchall()

            song_ids = {
                item['id']: IDs.encode_song({**item, 'path': os.fsdecode(item['path'] or b'')}) for item in items
            }

            failing_before = _failing_checks(db)

            counts.update(scan_errors(db, items, song_ids, full))
            counts['quality'] = scan_quality(db, song_ids)

            failing_after = _failing_checks(db)

            new_issues = len(failing_after - failing_before)

    except Exception as e:
        notify('health_scan_failed', details=str(e))
        raise

    finally:
        health_scan.release()
        admin_events.publish('health-scan', '')

    checked, missing = startup_path_check(music_root)
    if missing:
        notify('storage_problem', music_root=music_root, songs_sampled=checked, songs_missing=missing)

    stats = health_stats()

    notify('health_scan_completed',
           songs_checked=counts['checked'],
           songs_flagged=stats['flagged'],
           songs_unchanged=counts['skipped'],
           quality_issues=stats['quality'],
           new_issues=new_issues)

    return counts


@with_app_context
def startup_path_check(music_root: str | Path, sample_size: int = 20) -> tuple[int, int]:
    """
    Stats a random sample of resolved item paths. Meant to be called once at boot,
    so a bad path-mapping/mount config is caught fast.

    Returns (checked, missing) counts.
    """
    with dual_database() as db:
        rows = db.execute(
            """
            SELECT path 
            FROM beets.items 
            ORDER BY RANDOM() LIMIT ?
            """, (sample_size,)
        ).fetchall()

    checked = 0
    missing = 0
    for row in rows:
        path = os.fsdecode(row['path'] or b'')
        if not path:
            continue
        checked += 1
        if not resolve_path(path, music_root).exists():
            missing += 1

    return checked, missing


def detect_beets_drift(db_path: str | Path) -> dict[str, dict]:
    """
    Compares items/albums tables in a beets db against this install's expected schema, before Library() opens it.
    """

    models = {'items': Item, 'albums': Album}
    migrations = {'items': set(), 'albums': set()}

    for migration_cls, model_classes in BeetsLibrary._migrations:
        for model_cls in model_classes:
            for table, cls in models.items():
                if model_cls is cls:
                    migrations[table].add(migration_cls.name)

    expected = {
        table: {'columns': set(cls._fields), 'migrations': migrations[table]}
        for table, cls in models.items()
    }

    result = {}

    conn = sqlite3.connect(f'file:{Path(db_path).as_posix()}?mode=ro', uri=True)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        for table in models:
            if table not in tables:
                continue

            if 'migrations' in tables:
                recorded = {
                    row[0] for row in
                    conn.execute(
                        """
                        SELECT name 
                        FROM migrations 
                        WHERE table_name = ?
                        """, (table,)
                    )
                }
                unknown_migrations = sorted(recorded - expected[table]['migrations'])
                if unknown_migrations:
                    result[table] = {'unknown_migrations': unknown_migrations}
                continue

            actual_columns = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
            unknown_columns = sorted(actual_columns - expected[table]['columns'])
            missing_columns = sorted(expected[table]['columns'] - actual_columns)

            if unknown_columns or missing_columns:
                result[table] = {'unknown_columns': unknown_columns, 'missing_columns': missing_columns}

    finally:
        conn.close()

    return result


def start_health_scan(full: bool = False) -> tuple[bool, str]:
    """
    Starts a health scan on a background thread. Returns (started, message).
    """
    if health_scan.running:
        return False, 'A health scan is already running.'

    def _run():
        counts = scan_library(full=full)
        bsn_logger.info(
            f"Health scan complete: {counts['checked']} checked, "
            f"{counts['flagged']} flagged, {counts['skipped']} unchanged (skipped), "
            f"{counts['pruned']} stale entries pruned, {counts['quality']} quality findings."
        )

    threading.Thread(target=_run, daemon=True).start()
    return True, 'Health scan started.'


def get_server_info(extended: bool = False) -> dict[str, str]:
    lib = app.config['lib']
    stats = {}
    with lib.transaction() as tx:
        stats['artists'] = tx.query("SELECT COUNT(DISTINCT albumartist) FROM albums")[0][0]
        stats['albums'] = tx.query("SELECT COUNT(*) FROM albums")[0][0]
        stats['songs'] = tx.query("SELECT COUNT(*) FROM items")[0][0]

    if extended:
        ffmpeg_path = find_binary('ffmpeg')
        mpv_path = find_binary('mpv')

        try:
            cfg_path = str(config_path())
        except Exception:
            cfg_path = 'default location'

        additional_info = {
            'version': SERVER_VERSION,
            'beets_version': beets.__version__,
            'python_version': platform.python_version(),
            'os': platform.system(),
            'uptime': format_elapsed(time.time() - START_TIME),
            'db_path': str(app.config.get('BSN_DB_PATH')),
            'library_path': str(app.config.get('BEETS_DB_PATH')),
            'config_path': cfg_path,
            'ffmpeg_path': ffmpeg_path or 'not found',
            'ffmpeg_version': (binary_version(ffmpeg_path, '-version') or 'unknown') if ffmpeg_path else None,
            'mpv_path': mpv_path or 'not found',
            'mpv_version': (binary_version(mpv_path, '--version') or 'unknown') if mpv_path else None,
            'stats': stats,
        }
        stats.update(additional_info)

    return stats
