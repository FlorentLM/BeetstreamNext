from __future__ import annotations

import secrets
import time
from typing import Optional

import flask

from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.constants import WEB_SESSION_MAX_AGE_SEC, WEB_SESSION_LASTSEEN_SEC
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.text import sha256_hex


def current_session_id() -> Optional[str]:
    """
    Get the stored id of the current signed-in session.
    """
    sid = flask.session.get('sid')
    return sha256_hex(sid) if sid and isinstance(sid, str) else None


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


def list_user_sessions(username: str) -> list[dict]:
    """
    All existing sessions of `username`
    (id is the stored hash, not the cookie value)
    """
    now = time.time()

    with database() as db:
        rows = db.execute(
            """
            SELECT id, created, last_seen, ip, user_agent
            FROM web_sessions
            WHERE username = ? AND created > ? AND last_seen > ?
            ORDER BY last_seen DESC
            """, (username, now - WEB_SESSION_MAX_AGE_SEC, now - app.permanent_session_lifetime.total_seconds())
        ).fetchall()

    return [dict(row) for row in rows]


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


def revoke_session(session_id: Optional[str], username: Optional[str] = None) -> bool:

    if not session_id:
        return False

    with database() as db:
        cursor = db.execute(
            """
            DELETE FROM web_sessions
            WHERE id = ? AND (? IS NULL OR username = ?)
            """, (session_id, username, username)
        )
    return cursor.rowcount > 0


def revoke_user_sessions(username: str, keep: Optional[str] = None) -> int:
    """Sign out `username` everywhere (except `keep` session id)."""
    with database() as db:
        cursor = db.execute(
            """
            DELETE FROM web_sessions
            WHERE username = ? AND id != ?
            """, (username, keep or '')
        )
    return cursor.rowcount
