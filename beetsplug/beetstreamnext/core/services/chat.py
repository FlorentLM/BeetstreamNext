from __future__ import annotations

import time
from typing import Optional

from beetsplug.beetstreamnext.constants import (
    CHAT_MAX_CHARS, CHAT_MAX_STORED, CHAT_RATE_MAX, CHAT_RATE_WINDOW_SEC, CHAT_RETENTION_SEC
)
from beetsplug.beetstreamnext.core.services.events import admin_events
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.concurrency import RollingLimiter
from beetsplug.beetstreamnext.utils.text import safe_str


CHAT_PAGE_SIZE = 50

_limiter = RollingLimiter(CHAT_RATE_MAX, CHAT_RATE_WINDOW_SEC)


class ChatError(Exception):
    """A message that can't be posted."""


def clean_message(message: str) -> str:

    message = safe_str(message or '').strip()

    if not message:
        raise ChatError('Message cannot be empty.')

    if len(message) > CHAT_MAX_CHARS:
        raise ChatError(f'Message exceeds maximum length ({CHAT_MAX_CHARS} characters).')

    return message


def post_message(username: str, message: str) -> None:
    """
    Rate-limited insert, and the retention pass.
    Used by the Subsonic endpoint and by the web UI.
    """
    message = clean_message(message)

    now = time.time()
    now_ms = int(now * 1000)

    if not _limiter.allow(username, now):
        raise ChatError('Too many messages, slow down.')

    with database() as db:
        db.execute(
            """
            INSERT INTO chat_messages (username, time, message)
            VALUES (?, ?, ?)
            """, (username, now_ms, message)
        )

        # Drop messages older than 30 days
        db.execute(
            """
            DELETE FROM chat_messages
            WHERE time < ?
            """, (now_ms - CHAT_RETENTION_SEC * 1000,)
        )

        # Drop oldest ones if over the cap
        db.execute(
            """
            DELETE FROM chat_messages
            WHERE id NOT IN (SELECT id FROM chat_messages ORDER BY time DESC, id DESC LIMIT ?)
            """, (CHAT_MAX_STORED,)
        )

    admin_events.publish('chat', '')


def get_message(msg_id: int) -> Optional[dict]:

    with database() as db:
        row = db.execute(
            """
            SELECT m.id, m.username, m.time, m.message, CAST(u.avatarLastChanged AS INTEGER) AS avatar
            FROM chat_messages m
            LEFT JOIN users u ON u.username = m.username
            WHERE m.id = ?
            """, (msg_id,)
        ).fetchone()

    return dict(row) if row else None


def fetch_messages(after_id: Optional[int] = None, before_id: Optional[int] = None) -> tuple[list[dict], bool]:
    """
    Returns (messages, has_older), oldest first.

    No arg: returns newest page of messages
        - before_id: return page of messages older than this id
        - after_id: return all messages newer than this id (has_older has to be False)
    """

    with database() as db:
        if after_id is not None:
            rows = db.execute(
                """
                SELECT m.id, m.username, m.time, m.message, CAST(u.avatarLastChanged AS INTEGER) AS avatar
                FROM chat_messages m
                LEFT JOIN users u ON u.username = m.username
                WHERE m.id > ?
                ORDER BY m.id
                LIMIT ?
                """, (after_id, 200)
            ).fetchall()
            return [dict(r) for r in rows], False

        rows = db.execute(
            """
            SELECT m.id, m.username, m.time, m.message, CAST(u.avatarLastChanged AS INTEGER) AS avatar
            FROM chat_messages m
            LEFT JOIN users u ON u.username = m.username
            WHERE (? IS NULL OR m.id < ?)
            ORDER BY m.id DESC
            LIMIT ?
            """, (before_id, before_id, CHAT_PAGE_SIZE + 1)
        ).fetchall()

    has_older = len(rows) > CHAT_PAGE_SIZE
    return [dict(r) for r in reversed(rows[:CHAT_PAGE_SIZE])], has_older


def edit_message(msg_id: int, message: str) -> bool:

    message = clean_message(message)

    with database() as db:
        updated = db.execute(
            """
            UPDATE chat_messages
            SET message = ?
            WHERE id = ?
            """, (message, msg_id)
        ).rowcount

    admin_events.publish('chat', '')
    return bool(updated)


def delete_message(msg_id: int) -> None:

    with database() as db:
        db.execute(
            """
            DELETE FROM chat_messages
            WHERE id = ?
            """, (msg_id,)
        )

    admin_events.publish('chat', '')
