from __future__ import annotations

import time
import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.utils.text import safe_str


ANNOUNCEMENT_USERNAME = 'Server'
CHAT_MESSAGE_MAX_LEN = 1000
CHAT_PAGE_SIZE = 50


def chat_page_context(page: int = 1) -> dict:
    """Template variables for one page of chat moderation (page is clamped to valid range)."""

    with database() as db:
        total = db.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]

        pages = max(1, -(-total // CHAT_PAGE_SIZE))
        page = min(max(1, page), pages)

        messages = db.execute(
            """
            SELECT id, username, time, message
            FROM chat_messages
            ORDER BY time DESC
            LIMIT ? OFFSET ?
            """, (CHAT_PAGE_SIZE, (page - 1) * CHAT_PAGE_SIZE)
        ).fetchall()

    return {'chat_messages': messages, 'chat_page': page, 'chat_pages': pages}


def _chat_partial(message: str | None = None, ok: bool = True) -> str:
    page = flask.request.args.get('chat_page', default=1, type=int)
    return flask.render_template('partials/chat_table.html', message=message, ok=ok, **chat_page_context(page))


@admin_bp.route('/chat', methods=['GET'])
@admin_required
def route_chat() -> str:
    return _chat_partial()


@admin_bp.route('/chat/announce', methods=['POST'])
@admin_required
def route_add_announcement() -> str:
    message = safe_str(flask.request.form.get('message', '').strip())

    if not message:
        return _chat_partial('Announcement cannot be empty.', ok=False)

    if len(message) > CHAT_MESSAGE_MAX_LEN:
        return _chat_partial(f'Announcement exceeds maximum length ({CHAT_MESSAGE_MAX_LEN} characters).', ok=False)

    with database() as db:
        db.execute(
            """
            INSERT INTO chat_messages (username, time, message)
            VALUES (?, ?, ?)
            """, (ANNOUNCEMENT_USERNAME, int(time.time() * 1000), message)
        )

    return _chat_partial('Announcement posted.')


@admin_bp.route('/chat/delete/<int:msg_id>', methods=['POST'])
@admin_required
def route_delete_chat_message(msg_id: int) -> str:
    with database() as db:
        db.execute(
            """
            DELETE FROM chat_messages
            WHERE id = ?
            """, (msg_id,)
        )

    return _chat_partial('Chat message deleted.')


@admin_bp.route('/chat/edit/<int:msg_id>', methods=['POST'])
@admin_required
def route_edit_chat_message(msg_id: int) -> str:
    new_message = safe_str(flask.request.form.get('message', '').strip())

    if not new_message:
        return _chat_partial('Message cannot be empty.', ok=False)

    with database() as db:
        db.execute(
            """
            UPDATE chat_messages
            SET message = ?
            WHERE id = ?
            """, (new_message, msg_id)
        )

    return _chat_partial('Chat message updated.')
