from __future__ import annotations

import secrets
import time
from typing import Optional

from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.constants import WEB_SESSION_MAX_AGE_SEC, WEB_SESSION_LASTSEEN_SEC
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.text import sha256_hex


def create_session(username: str, ip: Optional[str] = None, user_agent: Optional[str] = None) -> str:
    """Records a new login and return its id (to put in the cookie)."""

    sid = secrets.token_urlsafe(32)
    now = time.time()

    with database() as db:
        db.execute(
            """
            INSERT INTO web_sessions (id, username, created, last_seen, ip, user_agent)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (sha256_hex(sid), username, now, now, ip, (user_agent or '')[:200])
        )

    return sid


def validate_session(sid: Optional[str], username: str, touch: bool = True) -> bool:
    """Whether `sid` is a live session of `username`."""

    if not sid or not isinstance(sid, str):
        return False

    now = time.time()
    key = sha256_hex(sid)

    with database() as db:
        row = db.execute(
            """
            SELECT username, created, last_seen
            FROM web_sessions
            WHERE id = ?
            """, (key,)
        ).fetchone()

        if row is None or row['username'] != username:
            return False

        if now - row['created'] > WEB_SESSION_MAX_AGE_SEC or now - row['last_seen'] > app.permanent_session_lifetime.total_seconds():
            db.execute("""DELETE FROM web_sessions WHERE id = ?""", (key,))
            return False

        if touch and now - row['last_seen'] > WEB_SESSION_LASTSEEN_SEC:
            db.execute("""UPDATE web_sessions SET last_seen = ? WHERE id = ?""", (now, key))

    return True


def revoke_session(sid: Optional[str]) -> None:
    if not sid or not isinstance(sid, str):
        return

    with database() as db:
        db.execute("""DELETE FROM web_sessions WHERE id = ?""", (sha256_hex(sid),))


def revoke_user_sessions(username: str, keep: Optional[str] = None) -> int:
    """Sign out `username` everywhere (except the `keep` session)."""
    with database() as db:
        cursor = db.execute(
            """
            DELETE FROM web_sessions
            WHERE username = ? AND id != ?
            """, (username, sha256_hex(keep) if keep else '')
        )
    return cursor.rowcount


def count_active_sessions(username: str, exclude: Optional[str] = None) -> int:
    now = time.time()

    with database() as db:
        row = db.execute(
            """
            SELECT COUNT(*)
            FROM web_sessions
            WHERE username = ? AND id != ? AND created > ? AND last_seen > ?
            """, (username, sha256_hex(exclude) if exclude else '', now - WEB_SESSION_MAX_AGE_SEC, now - app.permanent_session_lifetime.total_seconds())
        ).fetchone()

    return row[0]


@with_app_context
def purge_expired_sessions() -> int:
    now = time.time()

    with database() as db:
        cursor = db.execute(
            """
            DELETE FROM web_sessions
            WHERE created < ? OR last_seen < ?
            """, (now - WEB_SESSION_MAX_AGE_SEC, now - app.permanent_session_lifetime.total_seconds())
        )
    return cursor.rowcount
