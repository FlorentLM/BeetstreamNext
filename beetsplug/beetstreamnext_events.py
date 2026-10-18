"""
Tiny companion plugin for beets that reports per-album import results to BeetstreamNext.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Optional, Tuple

from beets.importer import Action
from beets.plugins import BeetsPlugin


EVENTS_ENV_VAR = 'BSN_IMPORT_EVENTS'


class BeetstreamNextEvents(BeetsPlugin):

    def __init__(self) -> None:
        super().__init__()

        self._path: Optional[str] = os.environ.get(EVENTS_ENV_VAR) or None
        self._lock = threading.Lock()
        self._pending: Dict[int, Dict[str, Any]] = {}

        if self._path:
            self.register_listener('import_task_choice', self._on_choice)
            self.register_listener('import_task_files', self._on_files)
            self.register_listener('cli_exit', self._on_exit)

    def _write(self, record: Dict[str, Any]) -> None:
        try:
            line = json.dumps(record, ensure_ascii=False)
            with self._lock, open(self._path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')

        except (OSError, TypeError, ValueError) as e:
            self._log.debug('Could not report import result: {}', e)

    @staticmethod
    def _describe(task) -> Dict[str, Any]:

        paths = getattr(task, 'paths', None) or []
        path = paths[0] if paths else getattr(task, 'toppath', None) or b''

        if isinstance(path, bytes):
            path = os.fsdecode(path)

        first = next(iter(getattr(task, 'items', None) or []), None)

        artist = getattr(task, 'cur_artist', None) or (first and (first.get('albumartist') or first.get('artist'))) or ''
        album = getattr(task, 'cur_album', None) or (first and first.get('album')) or ''

        return {'artist': artist, 'album': album, 'path': str(path)}

    @staticmethod
    def _skip_reason(session, task) -> Tuple[str, str]:
        """Returns (cause, human-readable reason) for a skipped album."""

        # Set by cataloghint plugin when it is what made the match unconfident
        hint = getattr(task, 'cataloghint_reason', None)
        if hint:
            return 'cataloghint', str(hint)

        try:
            quiet = bool(session.config['quiet'].get(bool))
        # We should not break the import for this so all exceptions are caught
        except Exception:   # noqa: BLE001
            quiet = False

        if not quiet:
            return 'user', 'Skipped by you'

        candidates = getattr(task, 'candidates', None) or []
        if not candidates:
            return 'no_candidates', 'No match found'

        try:
            similarity = round((1 - float(candidates[0].distance)) * 100)
            return 'weak_match', f'Best match only {similarity}% similar, not confident enough'
        except (AttributeError, TypeError, ValueError):
            return 'weak_match', 'No confident match'

    def _on_choice(self, session, task) -> None:

        # Incremental skips never reach this event
        if not getattr(task, 'is_album', True):
            return      # Singletons are not albums

        info = self._describe(task)

        if task.choice_flag is Action.SKIP:
            cause, reason = self._skip_reason(session, task)
            self._write({'event': 'skipped', 'cause': cause, 'reason': reason, **info})
            return

        # May still be dropped later (duplicate etc)
        with self._lock:
            self._pending[id(task)] = info

    def _on_files(self, session, task) -> None:
        """Only tasks that survived duplicate resolution get here."""
        if not getattr(task, 'is_album', True):
            return

        with self._lock:
            info = self._pending.pop(id(task), None)

        if info is None:    # No choice stage (e.g. autotag off)
            info = self._describe(task)

        self._write({'event': 'imported', 'tracks': len(getattr(task, 'imported_items', lambda: [])()), **info})

    def _on_exit(self, lib) -> None:
        with self._lock:
            leftover = list(self._pending.values())
            self._pending.clear()

        for info in leftover:
            self._write({'event': 'skipped', 'cause': 'duplicate', 'reason': 'Already in the library', **info})
