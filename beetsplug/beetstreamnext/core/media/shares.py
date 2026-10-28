from __future__ import annotations

from datetime import date, datetime, time as dtime
from typing import Optional, List
import flask

from beetsplug.beetstreamnext.constants import MAX_UNLOCKED_SHARES
from beetsplug.beetstreamnext.core.accounts.passwords import hash_password, verify_password
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.runtime.web import external_url
from beetsplug.beetstreamnext.utils.text import sha256_hex


SHARE_SESSION_KEY = 'unlocked_shares'


def list_shares(username: Optional[str] = None, share_id: Optional[str] = None) -> List[dict]:
    """Shares with their public URL (newest first)."""

    conditions = []
    params = []

    if username is not None:
        conditions.append('s.username = ?')
        params.append(username)

    if share_id is not None:
        conditions.append('s.id = ?')
        params.append(share_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ''

    with database() as db:

        rows = db.execute(
            f"""
            SELECT s.id, s.username, s.description, s.expires, s.created, s.visit_count,
                   s.max_downloads, s.download_count,
                   (s.password_hash IS NOT NULL) AS has_password,
                   (SELECT COUNT(*) FROM share_entries se WHERE se.share_id = s.id AND se.item_id LIKE 'al-%') AS album_count,
                   (SELECT COUNT(*) FROM share_entries se WHERE se.share_id = s.id AND se.item_id LIKE 'sg-%') AS song_count
            FROM shares s
            {where}
            ORDER BY s.created DESC
            """, params  # nosec B608
        ).fetchall()

    shares = []
    for row in rows:
        share = dict(row)
        share['url'] = external_url(flask.url_for('public.share_view', share_id=share['id']))
        shares.append(share)

    return shares


def delete_share(share_id: str, username: Optional[str] = None) -> None:
    """Delete a share. With `username`, only delete ones that belongs to that user."""

    with database() as db:

        db.execute(
            f"""
            DELETE FROM shares
            WHERE id = ? {'AND username = ?' if username is not None else ''}
            """, (share_id, username) if username is not None else (share_id,)  # nosec B608
        )


def update_share(
        share_id: str,
        username: Optional[str],
        *,
        description: str,
        expires_on: Optional[date],
        max_downloads: Optional[int],
        new_password: Optional[str] = None,
        remove_password: bool = False
    ) -> bool:
    """
    Edit a share's description, expiry, download limit and password.
    """

    expires = datetime.combine(expires_on, dtime.max).timestamp() if expires_on else None

    assignments = ['description = ?', 'expires = ?', 'max_downloads = ?']
    params: list = [description, expires, max_downloads or None]

    if new_password:
        assignments.append('password_hash = ?')
        params.append(hash_password(new_password))

    elif remove_password:
        assignments.append('password_hash = NULL')

    params.append(share_id)
    if username is not None:
        params.append(username)

    with database() as db:
        cursor = db.execute(
            f"""
            UPDATE shares SET {', '.join(assignments)}
            WHERE id = ? {'AND username = ?' if username is not None else ''}
            """, params  # nosec B608
        )

    return cursor.rowcount > 0


def claim_download(share_id: str) -> bool:
    """Count a download against the share's limit."""

    with database() as db:
        cursor = db.execute(
            """
            UPDATE shares
            SET download_count = download_count + 1
            WHERE id = ? AND (max_downloads IS NULL OR download_count < max_downloads)
            """, (share_id,)
        )

    return cursor.rowcount > 0


def unlock_share(share, password: str) -> bool:
    """Check the password and remember the unlock in the visitor's session."""

    if not verify_password(password, share['password_hash']):
        return False

    unlocked = dict(flask.session.get(SHARE_SESSION_KEY, {}))
    unlocked.pop(share['id'], None)      # Re-insert last so it is the newest

    unlocked[share['id']] = sha256_hex(share['password_hash'])      # Changing the password locks everyone out
    flask.session[SHARE_SESSION_KEY] = dict(list(unlocked.items())[-MAX_UNLOCKED_SHARES:])

    return True


def is_unlocked(share) -> bool:

    if not share['password_hash']:
        return True

    stamp = flask.session.get(SHARE_SESSION_KEY, {}).get(share['id'])
    return stamp == sha256_hex(share['password_hash'])

