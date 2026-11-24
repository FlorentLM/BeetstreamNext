from __future__ import annotations

import json
import time
from collections import defaultdict
from itertools import groupby
from dataclasses import asdict
from typing import Any

from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.constants import SCROBBLE_MAX_AGE_SEC, SCROBBLE_MIN_TRACK_SEC
from beetsplug.beetstreamnext.core.accounts.users_crud import get_userdata
from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.library.resolve import Resolve
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.scrobbling import SCROBBLERS
from beetsplug.beetstreamnext.core.services.scrobbling.base import ScrobbleTrack, ScrobbleStatus
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.storage.encryption import decrypt_json, encrypt_json
from beetsplug.beetstreamnext.utils.concurrency import WorkerPool


workers = WorkerPool(1, name='scrobble')


def is_allowed(username: str) -> bool:
    """Scrobbling is enabled globally and the user is allowed."""
    if not settings_store.get('external_scrobbling'):
        return False
    return bool((get_userdata(username, fields=['scrobblingEnabled']) or {}).get('scrobblingEnabled'))


def save_connection(username: str, service: str, credentials: dict[str, Any], display_name: str) -> None:
    """Reconnecting clears any error and the listens waiting are sent."""

    with database() as db:
        db.execute(
            """
            INSERT INTO user_connections (username, service, credentials_encrypted, display_name, last_ok)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (username, service) DO UPDATE SET
                credentials_encrypted = excluded.credentials_encrypted,
                display_name          = excluded.display_name,
                last_ok               = excluded.last_ok,
                last_error            = NULL,
                enabled               = 1
            """, (username, service, encrypt_json(credentials), display_name, time.time())
        )
    workers.submit(deliver_queued)


def delete_connection(username: str, service: str) -> bool:
    """Deletes the connection and all its queued listens."""

    with database() as db:
        return db.execute(
            """
            DELETE FROM user_connections 
            WHERE username = ? AND service = ?
            """, (username, service)
        ).rowcount > 0


def set_enabled(username: str, service: str, enabled: bool) -> None:
    """Turning a service back on sends what was waiting."""

    with database() as db:
        db.execute(
            """
            UPDATE user_connections 
            SET enabled = ? 
            WHERE username = ? AND service = ?
            """, (int(enabled), username, service)
        )
    if enabled:
        workers.submit(deliver_queued)


@with_app_context
def set_push_feedback(username: str, service: str, push: bool) -> None:
    """Whether song likes and dislikes are also sent to the service."""

    with database() as db:
        changed = db.execute(
            """
            UPDATE user_connections
            SET push_feedback = ?
            WHERE username = ? AND service = ? AND push_feedback != ?
            """, (int(push), username, service, int(push))
        ).rowcount

        scores = {}
        if changed and push:
            for row in db.execute("""SELECT item_id FROM likes WHERE username = ?""", (username,)):
                scores[row['item_id']] = 1
            if SCROBBLERS[service].has_dislikes:
                for row in db.execute("""SELECT item_id FROM dislikes WHERE username = ?""", (username,)):
                    scores[row['item_id']] = -1

    if not scores or 'push_feedback' not in SCROBBLERS[service].capabilities or not is_allowed(username):
        return

    song_ids = [item_id for item_id in scores if IDs.decode_type(item_id) == 'song']
    now = time.time()

    queued = []
    for song_id, item in Resolve.songs(song_ids).items():
        track = ScrobbleTrack.from_item(item, now)
        queued.append((json.dumps({**asdict(track), 'score': scores[song_id]}), track.played_at, username, service))

    with database() as db:
        db.executemany(
            """
            INSERT INTO scrobble_queue (username, service, kind, payload_json, played_at)
            SELECT username, service, 'feedback', ?, ?
            FROM user_connections
            WHERE username = ? AND service = ? AND last_error IS NULL AND enabled = 1
            """, queued
        )

    if queued:
        workers.submit(deliver_queued)


def get_credentials(username: str, service: str) -> dict[str, Any]:

    with database() as db:
        row = db.execute(
            """
            SELECT credentials_encrypted 
            FROM user_connections 
            WHERE username = ? AND service = ?
            """, (username, service)
        ).fetchone()

    return decrypt_json(row['credentials_encrypted']) if row else {}


def list_connections(username: str) -> list[dict[str, Any]]:

    with database() as db:
        rows = db.execute(
            """
            SELECT c.service, c.display_name, c.created, c.last_ok, c.last_error, c.enabled, c.push_feedback,
                   c.credentials_encrypted,
                   (SELECT COUNT(*) FROM scrobble_queue q WHERE q.username = c.username AND q.service = c.service) AS queued
            FROM user_connections c
            WHERE c.username = ?
            ORDER BY c.service
            """, (username,)
        ).fetchall()

    connections = []
    for r in rows:
        conn = dict(r)
        conn['url'] = decrypt_json(conn.pop('credentials_encrypted')).get('url', '')
        connections.append(conn)
    return connections


def _now_playing_work(targets: list[tuple], track: ScrobbleTrack) -> None:
    with app.app_context():
        for scrobbler, credentials in targets:
            try:
                scrobbler.now_playing(credentials, track)
            except Exception as e:
                # Failure is not worth retrying (and a refused token is noticed by the next delivery)
                bsn_logger.warning(f'Now-playing to {scrobbler.label} failed: {type(e).__name__}')


def now_playing(username: str, item: Any) -> None:

    track = ScrobbleTrack.from_item(item)
    if not (track.artist and track.title and is_allowed(username)):
        return

    with database() as db:
        rows = db.execute(
            """
            SELECT service, credentials_encrypted 
            FROM user_connections 
            WHERE username = ? AND last_error IS NULL AND enabled = 1
            """, (username,)
        ).fetchall()

    targets = [(SCROBBLERS[r['service']], decrypt_json(r['credentials_encrypted'])) for r in rows if r['service'] in SCROBBLERS]
    if targets:
        workers.submit(_now_playing_work, targets, track)


def scrobble(username: str, item: Any, played_at: float) -> None:

    track = ScrobbleTrack.from_item(item, played_at)

    if not (track.artist and track.title):
        return
    if 0 < track.duration_s < SCROBBLE_MIN_TRACK_SEC:
        return
    if time.time() - played_at > SCROBBLE_MAX_AGE_SEC:
        return
    if not is_allowed(username):
        return

    with database() as db:
        queued = db.execute(
            """
            INSERT INTO scrobble_queue (username, service, payload_json, played_at)
            SELECT username, service, ?, ? 
            FROM user_connections 
            WHERE username = ? AND last_error IS NULL AND enabled = 1
            """, (json.dumps(asdict(track)), played_at, username)
        ).rowcount

    if queued:
        workers.submit(deliver_queued)


def send_feedback(username: str, item_id: str, score: int) -> None:
    """Queues a song's like or dislike."""

    services = [name for name, s in SCROBBLERS.items() if 'push_feedback' in s.capabilities]
    if not services or IDs.decode_type(item_id) != 'song' or not is_allowed(username):
        return

    item = Resolve.song(item_id)
    if item is None:
        return

    track = ScrobbleTrack.from_item(item, time.time())

    with database() as db:
        queued = db.execute(
            f"""
            INSERT INTO scrobble_queue (username, service, kind, payload_json, played_at)
            SELECT username, service, 'feedback', ?, ?
            FROM user_connections
            WHERE username = ? AND last_error IS NULL AND enabled = 1 AND push_feedback = 1
              AND service IN ({','.join('?' * len(services))})
            """, (json.dumps({**asdict(track), 'score': score}), track.played_at, username, *services)  # nosec B608  # noqa: S608 placeholders only
        ).rowcount

    if queued:
        workers.submit(deliver_queued)


def deliver_queued() -> None:
    """Sends every queued listen of the working connections (on the calling thread)."""

    with app.app_context():
        with database() as db:
            db.execute(
                """
                DELETE FROM scrobble_queue 
                WHERE played_at < ?
                """, (time.time() - SCROBBLE_MAX_AGE_SEC,)
            )

            # Broken connections keep the listens until user reconnects
            due = db.execute(
                """
                SELECT q.id, q.username, q.service, q.kind, q.payload_json, c.credentials_encrypted
                FROM scrobble_queue q
                JOIN user_connections c ON c.username = q.username AND c.service = q.service
                WHERE c.last_error IS NULL AND c.enabled = 1
                ORDER BY q.played_at, q.id
                LIMIT 1000
                """
            ).fetchall()

        per_connection: dict[tuple, list] = defaultdict(list)
        for row in due:
            per_connection[(row['username'], row['service'])].append(row)

        for (username, service), rows in per_connection.items():
            scrobbler = SCROBBLERS.get(service)
            if scrobbler is None:
                continue

            credentials = decrypt_json(rows[0]['credentials_encrypted'])

            batches = []
            for kind, run in groupby(rows, key=lambda row: row['kind']):
                run = list(run)
                size = scrobbler.batch_size if kind == 'listen' else 1
                batches += [run[i:i + size] for i in range(0, len(run), size)]

            for batch in batches:
                payloads = [json.loads(row['payload_json']) for row in batch]

                try:
                    if batch[0]['kind'] == 'feedback':
                        score = payloads[0].pop('score')
                        status = scrobbler.push_feedback(credentials, ScrobbleTrack(**payloads[0]), score)
                    else:
                        status = scrobbler.submit(credentials, [ScrobbleTrack(**payload) for payload in payloads])
                except Exception as e:
                    bsn_logger.warning(f"Sending to {service} for '{username}' failed: {type(e).__name__}")
                    status = ScrobbleStatus.RETRY

                with database() as db:
                    if status in (ScrobbleStatus.OK, ScrobbleStatus.DROP, ScrobbleStatus.SKIP):
                        ids = [row['id'] for row in batch]
                        placeholders = ','.join('?' * len(ids))

                        db.execute(
                            f"""
                            DELETE FROM scrobble_queue 
                            WHERE id IN ({placeholders})
                            """, ids)   # nosec B608  # noqa: S608 vars are constants

                    if status is ScrobbleStatus.OK:
                        db.execute(
                            """
                            UPDATE user_connections 
                            SET last_ok = ? 
                            WHERE username = ? AND service = ?
                            """, (time.time(), username, service)
                        )
                    elif status is ScrobbleStatus.INVALID_AUTH:
                        # Stop sending until user reconnects
                        db.execute(
                            """
                            UPDATE user_connections 
                            SET last_error = ? 
                            WHERE username = ? AND service = ?
                            """, (f'{scrobbler.label} rejected the stored credentials.', username, service)
                        )

                if status is ScrobbleStatus.DROP:
                    bsn_logger.warning(f"{service} refused {len(batch)} item(s) of '{username}': dropped.")

                elif status is ScrobbleStatus.INVALID_AUTH:
                    bsn_logger.warning(f"{service} connection of '{username}' marked broken.")

                if status not in (ScrobbleStatus.OK, ScrobbleStatus.DROP, ScrobbleStatus.SKIP):
                    break       # Service is failing for this user. Next periodic run will retry