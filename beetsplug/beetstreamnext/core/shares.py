from __future__ import annotations

from typing import Optional
import flask

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.utils.general import external_url


def list_shares(username: Optional[str] = None) -> list[dict]:
    """Shares with their public URL (newest first)."""

    with database() as db:
        rows = db.execute(
            f"""
            SELECT s.id, s.username, s.description, s.expires, s.created, s.visit_count,
                   (SELECT COUNT(*) FROM share_entries se WHERE se.share_id = s.id) AS entry_count
            FROM shares s
            {'WHERE s.username = ?' if username is not None else ''}
            ORDER BY s.created DESC
            """, (username,) if username is not None else ()
        ).fetchall()

    return [{**dict(r), 'url': external_url(flask.url_for('public.share_view', share_id=r['id']))} for r in rows]


def delete_share(share_id: str, username: Optional[str] = None) -> None:
    """Delete a share. With `username`, only delete ones that belongs to that user."""

    with database() as db:
        db.execute(
            f"""
            DELETE FROM shares
            WHERE id = ? {'AND username = ?' if username is not None else ''}
            """, (share_id, username) if username is not None else (share_id,)
        )
