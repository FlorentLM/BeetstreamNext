import os
import re
import subprocess
import threading
from pathlib import Path

from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.core.database import database, dual_database
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.core.mappings import IDs
from beetsplug.beetstreamnext.utils.system import find_ffmpeg, resolve_path
from beetsplug.beetstreamnext.utils.text import format_duration

DECODE_ERRORS_CHECK = 'decode_errors'

_KIND_LABELS = {
    DECODE_ERRORS_CHECK: 'Decode errors',
}

_scan_lock = threading.Lock()
_scanning = False


def is_scanning() -> bool:
    return _scanning


_DTS_WARNING_RE = re.compile(r'non monotonically increasing dts to muxer in stream \d+: (\d+) >=')
_MAX_POSITIONS_SHOWN = 10


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
    ffmpeg_bin = find_ffmpeg() or 'ffmpeg'

    try:
        result = subprocess.run(
            [ffmpeg_bin, '-v', 'error', '-i', str(file_path), '-map', '0:a', '-f', 'null', '-'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f'probe failed: {e}'

    stderr = result.stderr.decode('utf-8', errors='replace')
    error_count = stderr.count('Decoding error:')

    if not error_count:
        return True, ''

    detail = f'{error_count} decode error{"s" if error_count != 1 else ""}'

    positions = _parse_error_positions(stderr, samplerate)
    if positions:
        shown = positions[:_MAX_POSITIONS_SHOWN]
        more = len(positions) - len(shown)
        detail += f" (near {', '.join(shown)})"
        if more:
            detail += f' (+{more} more)'

    return False, detail


def needs_healing(song_id: str) -> bool:
    """True if a song is flagged with an unresolved health issue (any kind)."""

    with database() as db:
        row = db.execute(
            """
            SELECT 1 
            FROM song_checks 
            WHERE song_id = ? AND ok = 0 
            LIMIT 1
            """, (song_id,)
        ).fetchone()

    return row is not None


def health_stats() -> dict[str, int]:
    """Counts sum of all health errors. Returns total songs checked, and how many are flagged."""

    with database() as db:
        row = db.execute(
            """
            SELECT COUNT(*) AS total, COALESCE(SUM(ok = 0), 0) AS flagged FROM song_checks
            """
        ).fetchone()

    return {'total': row['total'], 'flagged': row['flagged']}


@with_app_context
def flagged_songs() -> list[dict]:
    """
    Resolves currently flagged songs, with display info (most recently checked first)
    """

    from beetsplug.beetstreamnext.core.mappings import Resolve

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
        result.append({
            'title': item.get('title') if item else '(deleted)',
            'artist': item.get('artist') if item else '',
            'album': item.get('album') if item else '',
            'kind': row['kind'],
            'label': _KIND_LABELS.get(row['kind'], row['kind']),
            'detail': row['detail'],
            'checked_at': row['checked_at'],
        })

    return result


@with_app_context
def scan_library(full: bool = False) -> dict[str, int]:
    """
    Checks library audio files for health issues (currently just decode errors) incrementally.

    Args:
        - full: True to force checking everything
    """
    global _scanning

    counts = {'checked': 0, 'flagged': 0, 'skipped': 0}

    if not find_ffmpeg():
        bsn_logger.warning('Health scan skipped: ffmpeg not found.')
        return counts

    if not _scan_lock.acquire(blocking=False):
        bsn_logger.info('Health scan already running, skipping this trigger.')
        return counts

    _scanning = True
    root_directory = app.config['root_directory']

    try:
        with dual_database() as db:
            item_rows = db.execute(
                """
                SELECT id, mb_trackid, mb_releasetrackid, path, samplerate FROM beets.items
                """
            ).fetchall()

            existing: dict[str, tuple[float, int]] = {}
            if not full:
                existing = {
                    row['song_id']: (row['mtime'], row['ok'])
                    for row in db.execute(
                        """
                        SELECT song_id, mtime, ok 
                        FROM song_checks 
                        WHERE kind = ?
                        """, (DECODE_ERRORS_CHECK,)
                    ).fetchall()
                }

            for beets_id, mb_trackid, mb_releasetrackid, raw_path, samplerate in item_rows:
                path = os.fsdecode(raw_path or b'')
                if not path:
                    continue

                path_obj = resolve_path(path, root_directory)

                try:
                    mtime = os.path.getmtime(path_obj)
                except OSError:
                    continue   # file missing, nothing to check

                song_id = IDs.encode_song({
                    'id': beets_id,
                    'mb_trackid': mb_trackid,
                    'mb_releasetrackid': mb_releasetrackid,
                    'path': path
                })

                prev = existing.get(song_id)
                if prev is not None and prev[0] == mtime:
                    counts['skipped'] += 1
                    continue

                ok, detail = check_decode_errors(path_obj, samplerate=samplerate or 0)
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
                    """, (song_id, DECODE_ERRORS_CHECK, mtime, int(ok), detail)
                )
                db.commit()
    finally:
        _scanning = False
        _scan_lock.release()

    return counts


def start_scan(full: bool = False) -> tuple[bool, str]:
    """
    Starts a health scan on a background thread. Returns (started, message).
    """
    if is_scanning():
        return False, 'A health scan is already running.'

    def _run():
        counts = scan_library(full=full)
        bsn_logger.info(
            f"Health scan complete: {counts['checked']} checked, "
            f"{counts['flagged']} flagged, {counts['skipped']} unchanged (skipped)."
        )

    threading.Thread(target=_run, daemon=True).start()
    return True, 'Health scan started.'
