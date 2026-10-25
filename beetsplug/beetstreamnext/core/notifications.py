from __future__ import annotations

import threading
import time
from cryptography.fernet import InvalidToken
from dataclasses import dataclass
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import APPRISE
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.storage.encryption import get_cipher
from beetsplug.beetstreamnext.utils.general import api_bool


@dataclass(frozen=True)
class EventOption:
    """Event-specific setting."""

    key: str
    label: str
    kind: type = bool
    default: Any = False
    help: str = ''
    min: Optional[int] = None
    max: Optional[int] = None
    choices: Tuple[str, ...] = ()

    def coerce(self, raw: Any) -> Any:

        if self.kind is bool:
            return api_bool(raw)

        if self.kind is int:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return self.default
            if self.min is not None:
                value = max(self.min, value)
            if self.max is not None:
                value = min(self.max, value)
            return value

        if self.choices:
            return str(raw) if str(raw) in self.choices else self.default

        return str(raw)

    @property
    def widget(self) -> str:
        """Which input the Web UI form shows."""
        if self.kind is bool:
            return 'checkbox'
        if self.kind is int:
            return 'number'
        return 'select' if self.choices else 'text'


@dataclass(frozen=True)
class Event:
    """A kind of event to send as a notification."""

    label: str                      # Label hown in the UI (and default title)
    status: str                     # fills the {status} variable
    body: str                       # Default notification body
    variables: Tuple[str, ...]      # Names of the VARIABLES available for this event
    notify_type: str = 'info'       # Apprise type: info, success, warning or failure
    options: Tuple[EventOption, ...] = ()
    should_send: Optional[Callable[[Dict[str, Any], Dict[str, Any]], bool]] = None

    def default_options(self) -> Dict[str, Any]:
        return {o.key: o.default for o in self.options}

    def clean_options(self, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        raw = raw or {}
        return {
            o.key: o.coerce(raw[o.key])
            if o.key in raw else o.default
            for o in self.options
        }


## Variables and Events instantiation


VARIABLES = {
    'path': ('Folder being imported', '/music/Example Album'),
    'status': ('Started, Completed, Failed, Needs input or Aborted', None),
    'exit_code': ('Exit code of the beets import', 0),
    'duration': ('How long the import ran for', '1m 23s'),
    'albums_imported': ('Albums added to the library', 2),
    'albums_skipped': ('Albums skipped: no match, duplicates, or skipped by you', 1),
    'imported_list': ('Names of the imported albums', 'Example Artist - First Album\nExample Artist - Second Album'),
    'skipped_list': ('Names of the skipped albums', 'Unknown Artist - Mystery Album'),
    'time': ('When the notification was sent', None),
}


EVENTS = {
    'import_started': Event(
        label='Import started',
        status='started',
        body='Importing {path}',
        variables=('path', 'status', 'time')),

    'import_completed': Event(
        label='Import completed',
        status='completed',
        body='Imported {path} in {duration}: {albums_imported} imported, {albums_skipped} skipped',
        variables=tuple(VARIABLES),
        notify_type='success',
        options=(
            EventOption(
                key='skip_if_unchanged',
                label="Don't send if nothing changed",
                default=True,
                help='Skips the notification if the import added no albums to the library'),
        ),
        should_send=lambda options, v: not (options['skip_if_unchanged'] and not v.get('albums_imported'))),

    'import_failed': Event(
        label='Import failed',
        status='failed',
        body='Importing {path} failed (exit code {exit_code}) after {duration}',
        variables=tuple(VARIABLES),
        notify_type='failure'),

    'import_needs_input': Event(
        label='Import needs input',
        status='needs input',
        body='The import of {path} is waiting for your input',
        variables=('path', 'status', 'time'),
        notify_type='warning'),

    'import_queue_aborted': Event(
        label='Pinned scan aborted',
        status='aborted',
        body='The pinned scan stopped at {path}',
        variables=('path', 'status', 'time'),
        notify_type='failure'),
}


## Storage

def _encode_urls(urls: List[str]) -> Tuple[str, int]:
    raw = json.dumps(urls)
    return get_cipher().encrypt(raw.encode()).decode(), 1


def _decode_urls(stored: str, encrypted: int) -> List[str]:
    if encrypted:
        try:
            stored = get_cipher().decrypt(stored.encode()).decode()
        except InvalidToken:
            bsn_logger.warning('Could not decrypt notification URLs (BEETSTREAMNEXT_KEY changed?)')
            return []
    try:
        return [str(u) for u in json.loads(stored)]
    except ValueError:
        return []


def _decode_options(event: str, stored: Optional[str]) -> Dict[str, bool]:

    kind = EVENTS.get(event)
    if kind is None:
        return {}
    try:
        raw = json.loads(stored or '{}')
    except ValueError:
        raw = {}
    return kind.clean_options(raw if isinstance(raw, dict) else {})


def _row_to_notification(row) -> Dict[str, Any]:
    return {
        'id': row['id'],
        'event': row['event'],
        'title': row['title'],
        'body': row['body'],
        'urls': _decode_urls(row['urls'], row['encrypted']),
        'enabled': bool(row['enabled']),
        'options': _decode_options(row['event'], row['options']),
    }


def list_notifications(event: Optional[str] = None) -> List[Dict[str, Any]]:

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
                """, (event,)
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


def add_notification(event: str, title: str, body: str, urls: List[str],
                     options: Optional[Dict[str, Any]] = None, enabled: bool = True) -> int:

    kind, title, body, urls = _validate(event, title, body, urls)
    stored, encrypted = _encode_urls(urls)

    with database() as db:
        cursor = db.execute(
            """
            INSERT INTO notifications (event, title, body, urls, encrypted, enabled, options)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (event, title, body, stored, encrypted, int(enabled), json.dumps(kind.clean_options(options)))
        )

    return cursor.lastrowid


def update_notification(notification_id: int, event: str, title: str, body: str, urls: List[str],
                        options: Optional[Dict[str, Any]] = None) -> bool:

    kind, title, body, urls = _validate(event, title, body, urls)
    stored, encrypted = _encode_urls(urls)

    with database() as db:
        cursor = db.execute(
            """
            UPDATE notifications SET event = ?, title = ?, body = ?, urls = ?, encrypted = ?, options = ?
            WHERE id = ?
            """, (event, title, body, stored, encrypted, json.dumps(kind.clean_options(options)), notification_id)
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


## Sending

RE_VARIABLE = re.compile(r'\{(\w+)\}')


def render(notification: Dict[str, Any], **variables: Any) -> Tuple[str, str]:
    """
    Title and body from the notification's templates (unknown {variables} are kept as-is).
    """

    kind = EVENTS[notification['event']]
    values = {**dict.fromkeys(kind.variables, ''), 'status': kind.status}
    values.update({k: '' if v is None else v for k, v in variables.items() if k in kind.variables})

    def expand(template: str) -> str:
        return RE_VARIABLE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)

    return expand(notification['title']), expand(notification['body'])


LOGO_URL = 'https://raw.githubusercontent.com/FlorentLM/BeetstreamNext/main/beetsplug/beetstreamnext/web/static/images/logo.png'


def _asset():
    """Apprise asset using the BSN logo."""

    import apprise
    return apprise.AppriseAsset(
        image_path_mask=str(app.config['IMAGES_PATH'] / 'logo.png'),    # for services that embed the image
        image_url_mask=LOGO_URL,                                        # for services that fetch it by URL
        image_url_logo=LOGO_URL
    )


def _send(url: str, title: str, body: str, notify_type: str) -> bool:

    import apprise
    client = apprise.Apprise(asset=_asset())
    if not client.add(url):
        return False

    return bool(client.notify(title=title, body=body, notify_type=notify_type))


def _send_all(urls: List[str], title: str, body: str, notify_type: str) -> int:
    """Send to every URL. Returns how many failed."""

    failed = 0
    for url in urls:
        try:
            if not _send(url, title, body, notify_type):
                failed += 1
        except Exception as e:
            bsn_logger.warning(f'Notification delivery errored: {e}')
            failed += 1

    return failed


def _dispatch(event: str, variables: Dict[str, Any]) -> None:
    """Pipeline per saved notification: filter -> render -> send."""

    kind = EVENTS[event]

    try:
        with app.app_context():
            variables = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), **variables}
            for item in list_notifications(event):
                if not item['enabled']:
                    continue

                if kind.should_send and not kind.should_send(item['options'], variables):
                    continue

                title, body = render(item, **variables)

                failed = _send_all(item['urls'], title, body, kind.notify_type)
                if failed:
                    bsn_logger.warning(f"Notification #{item['id']} ('{item['title']}'): "
                                       f"{failed} of {len(item['urls'])} URL(s) failed to deliver.")

    except Exception as e:
        bsn_logger.error(f"Notification dispatch for '{event}' failed: {e}")


def notify(event: str, **variables: Any) -> None:
    """
    Fire-and-forget: delivery happens on its own thread.
    No-op when apprise not installed.
    """
    if not APPRISE:
        return
    threading.Thread(target=_dispatch, args=(event, variables), name='notify', daemon=True).start()


def send_test(event: str, title: str, body: str, urls: List[str],
              options: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """
    Test send what's currently in the form (saved or not) with sample values, synchronously.
    Options are accepted but ignored: a test is never suppressed.
    """

    if not APPRISE:
        return False, 'Apprise is not installed.'

    try:
        kind, title, body, urls = _validate(event, title, body, urls)
        samples = {k: VARIABLES[k][1] for k in kind.variables}
        samples['time'] = time.strftime('%Y-%m-%d %H:%M:%S')
        title, body = render({'event': event, 'title': title, 'body': body}, **samples)
    except ValueError as e:
        return False, str(e)

    failed = _send_all(urls, title, body, kind.notify_type)
    if not failed:
        return True, 'Test notification sent.' if len(urls) == 1 else f'Test notification sent to {len(urls)} URLs.'

    if failed == len(urls):
        return False, 'The service rejected the notification.' if len(urls) == 1 else 'No URL accepted the notification.'

    return False, f'{failed} of {len(urls)} URLs failed.'