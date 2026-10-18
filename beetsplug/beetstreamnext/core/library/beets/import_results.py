from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger

EVENTS_PLUGIN = 'beetstreamnext_events'     # tiny companion module in beetsplug/


def read_results(path: Path) -> Dict[str, List[Dict[str, Any]]]:

    results: Dict[str, List[Dict[str, Any]]] = {'imported': [], 'skipped': []}

    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return results

    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue

        if isinstance(record, dict) and record.get('event') in results:
            results[record['event']].append(record)

    return results


def album_label(record: Dict[str, Any]) -> str:
    artist, album = record.get('artist') or '', record.get('album') or ''

    if artist and album:
        return f'{artist} - {album}'

    return album or artist or Path(record.get('path') or '').name or 'Unknown album'


def summarize(results: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Notification variables from the results."""

    max_listed = 5

    def listing(records: List[Dict[str, Any]]) -> str:
        names = [album_label(r) + (f" ({r['reason']})" if r.get('reason') else '') for r in records]
        shown = '\n'.join(names[:max_listed])
        return shown + (f'\n... and {len(names) - max_listed} more' if len(names) > max_listed else '')

    return {
        'albums_imported': len(results['imported']),
        'albums_skipped': len(results['skipped']),
        'imported_list': listing(results['imported']),
        'skipped_list': listing(results['skipped']),
    }


def reset(path: Path) -> bool:
    try:
        path.write_text('', encoding='utf-8')
        return True
    except OSError as e:
        bsn_logger.warning(f'Could not reset import results file: {e}')
        return False
