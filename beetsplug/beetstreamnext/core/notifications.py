from __future__ import annotations

import threading
import time
from cryptography.fernet import InvalidToken
from dataclasses import dataclass
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import APPRISE
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.storage.encryption import get_cipher


@dataclass(frozen=True)
class Event:
    """A kind of event to send as a notification."""
    key: str        # db key
    label: str      # Label hown in the UI (and default title)
    status: str     # fills the {status} variable
    body: str       # Default notification body


IMPORT_STARTED = Event(
    key='import_started',
    label='Import started',
    status='started',
    body='Importing {path}')

IMPORT_COMPLETED = Event(
    key='import_completed',
    label='Import completed',
    status='completed',
    body='Imported {path} in {duration}')

IMPORT_FAILED = Event(
    key='import_failed',
    label='Import failed',
    status='failed',
    body='Importing {path} failed (exit code {exit_code}) after {duration}')

IMPORT_NEEDS_INPUT = Event(
    key='import_needs_input',
    label='Import needs input',
    status='needs input',
    body='The import of {path} is waiting for your input')

IMPORT_QUEUE_ABORTED = Event(
    key='import_queue_aborted',
    label='Pinned scan aborted',
    status='aborted',
    body='The pinned scan stopped at {path}')

EVENTS: Dict[str, Event] = {e.key: e for e in (
    IMPORT_STARTED, IMPORT_COMPLETED, IMPORT_FAILED, IMPORT_NEEDS_INPUT, IMPORT_QUEUE_ABORTED)}


# Variables available in titles and bodies (TODO: shared by every type for now but that would likely change)
VARIABLES: Dict[str, str] = {
    'path': 'Folder being imported',
    'status': 'Started, Completed, Failed, Needs input or Aborted',
    'exit_code': 'Exit code of the beets import (empty until it finishes)',
    'duration': 'How long the import ran for',
    'time': 'When the notification was sent',
}


# Storage

def _encode_urls(urls: List[str]) -> Tuple[str, int]:
    raw = json.dumps(urls)
    cipher = get_cipher()
    if cipher:
        return cipher.encrypt(raw.encode()).decode(), 1
    bsn_logger.warning('Storing notification URLs unencrypted (no key).')
    return raw, 0


def _decode_urls(stored: str, encrypted: int) -> List[str]:
    if encrypted:
        cipher = get_cipher()
        try:
            stored = cipher.decrypt(stored.encode()).decode() if cipher else ''
        except InvalidToken:
            bsn_logger.warning('Could not decrypt notification URLs (BEETSTREAMNEXT_KEY changed?).')
            return []
    try:
        return [str(u) for u in json.loads(stored)]
    except ValueError:
        return []


def _row_to_notification(row) -> Dict[str, Any]:
    return {
        'id': row['id'],
        'event': row['event'],
        'title': row['title'],
        'body': row['body'],
        'urls': _decode_urls(row['urls'], row['encrypted']),
        'enabled': bool(row['enabled']),
    }


def list_notifications(event: Optional[Event] = None) -> List[Dict[str, Any]]:

    with database() as db:
        if event is None:
            rows = db.execute(
                """
                SELECT * FROM notifications 
                ORDER BY id
                """
            ).fetchall()

        else:
            rows = db.execute(
                """
                SELECT * FROM notifications 
                WHERE event = ? 
                ORDER BY id
                """, (event.key,)
            ).fetchall()

    return [_row_to_notification(r) for r in rows]


def get_notification(notification_id: int) -> Optional[Dict[str, Any]]:

    with database() as db:
        row = db.execute(
            """
            SELECT * FROM notifications 
            WHERE id = ?
            """, (notification_id,)
        ).fetchone()

    return _row_to_notification(row) if row else None


def _validate(event: str, title: str, body: str, urls: List[str]) -> Tuple[Event, str, str, List[str]]:

    kind = EVENTS.get(event)

    if kind is None:
        raise ValueError('Pick a notification type.')

    title = ' '.join((title or '').split())
    body = (body or '').strip()
    urls = [u.strip() for u in urls or [] if u and u.strip()]

    if not title:
        raise ValueError('A title is required.')

    if not urls:
        raise ValueError('Add at least one URL.')

    for url in urls:
        if '://' not in url:
            raise ValueError(f'"{url}" is not a full Apprise URL (should look like "json://host/path" or "discord://id/token").')

        if APPRISE:
            import apprise
            if not apprise.Apprise().add(url):
                raise ValueError(f'Apprise does not recognise "{url}".')

    return kind, title, body, urls


def add_notification(event: str, title: str, body: str, urls: List[str], enabled: bool = True) -> int:

    kind, title, body, urls = _validate(event, title, body, urls)
    stored, encrypted = _encode_urls(urls)

    with database() as db:
        cursor = db.execute(
            """
            INSERT INTO notifications (event, title, body, urls, encrypted, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (kind.key, title, body, stored, encrypted, int(enabled))
        )

    return cursor.lastrowid


def update_notification(notification_id: int, event: str, title: str, body: str, urls: List[str]) -> bool:

    kind, title, body, urls = _validate(event, title, body, urls)
    stored, encrypted = _encode_urls(urls)

    with database() as db:
        cursor = db.execute(
            """
            UPDATE notifications SET event = ?, title = ?, body = ?, urls = ?, encrypted = ?
            WHERE id = ?
            """, (kind.key, title, body, stored, encrypted, notification_id)
        )

    return cursor.rowcount > 0


def toggle_enabled(notification_id: int, enabled: bool) -> bool:

    with database() as db:
        cursor = db.execute(
            """
            UPDATE notifications 
            SET enabled = ? 
            WHERE id = ?
            """, (int(enabled), notification_id)
        )

    return cursor.rowcount > 0


def delete_notification(notification_id: int) -> bool:

    with database() as db:
        cursor = db.execute(
            """
            DELETE FROM notifications 
            WHERE id = ?
            """, (notification_id,)
        )

    return cursor.rowcount > 0


# Sending

RE_VARIABLE = re.compile(r'\{(\w+)\}')


def render(notification: Dict[str, Any], **variables: Any) -> Tuple[str, str]:
    """
    Title and body from the notification's templates (unknown {variables} are kept as-is).
    """

    values = {**dict.fromkeys(VARIABLES, ''), 'status': EVENTS[notification['event']].status}
    values.update({k: '' if v is None else v for k, v in variables.items()})

    def expand(template: str) -> str:
        return RE_VARIABLE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)

    return expand(notification['title']), expand(notification['body'])


def _send(url: str, title: str, body: str) -> bool:

    import apprise
    client = apprise.Apprise()
    if not client.add(url):
        return False

    return bool(client.notify(title=title, body=body))


def _send_all(urls: List[str], title: str, body: str) -> int:
    """Send to every URL. Returns how many failed."""

    failed = 0
    for url in urls:
        try:
            if not _send(url, title, body):
                failed += 1
        except Exception as e:
            bsn_logger.warning(f'Notification delivery errored: {e}')
            failed += 1

    return failed


def _dispatch(event: Event, variables: Dict[str, Any]) -> None:

    try:
        with app.app_context():
            variables = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), **variables}
            for item in list_notifications(event):
                if not item['enabled']:
                    continue

                title, body = render(item, **variables)

                failed = _send_all(item['urls'], title, body)
                if failed:
                    bsn_logger.warning(f"Notification #{item['id']} ('{item['title']}'): "
                                       f"{failed} of {len(item['urls'])} URL(s) failed to deliver.")

    except Exception as e:
        bsn_logger.error(f"Notification dispatch for '{event.key}' failed: {e}")


def notify(event: Event, **variables: Any) -> None:
    """
    Fire-and-forget: delivery happens on its own thread.
    No-op when apprise not installed.
    """
    if not APPRISE:
        return
    threading.Thread(target=_dispatch, args=(event, variables), name='notify', daemon=True).start()


_SAMPLE_VARIABLES = {'path': '/music/Example Album', 'exit_code': 0, 'duration': '1m 23s'}


def send_test(event: str, title: str, body: str, urls: List[str]) -> Tuple[bool, str]:
    """
    Test send what's currently in the form (saved or not) with sample values, synchronously.
    """

    if not APPRISE:
        return False, 'Apprise is not installed.'

    try:
        kind, title, body, urls = _validate(event, title, body, urls)
        title, body = render({'event': kind.key, 'title': title, 'body': body},
                             time=time.strftime('%Y-%m-%d %H:%M:%S'), **_SAMPLE_VARIABLES)
    except ValueError as e:
        return False, str(e)

    failed = _send_all(urls, title, body)
    if not failed:
        return True, 'Test notification sent.' if len(urls) == 1 else f'Test notification sent to {len(urls)} URLs.'

    if failed == len(urls):
        return False, 'The service rejected the notification.' if len(urls) == 1 else 'No URL accepted the notification.'

    return False, f'{failed} of {len(urls)} URLs failed.'