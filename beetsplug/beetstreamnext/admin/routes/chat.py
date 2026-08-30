import time
import flask

from .. import admin_bp, admin_required, back_to

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.utils.text import safe_str


_ANNOUNCEMENT_USERNAME = 'Server'
_CHAT_MESSAGE_MAX_LEN = 1000


@admin_bp.route('/chat/announce', methods=['POST'])
@admin_required
def route_add_announcement() -> flask.Response:
    message = safe_str(flask.request.form.get('message', '').strip())

    if not message:
        flask.flash('Announcement cannot be empty.', 'error')
        return back_to('chat')

    if len(message) > _CHAT_MESSAGE_MAX_LEN:
        flask.flash(f'Announcement exceeds maximum length ({_CHAT_MESSAGE_MAX_LEN} characters).', 'error')
        return back_to('chat')

    with database() as db:
        db.execute(
            """
            INSERT INTO chat_messages (username, time, message)
            VALUES (?, ?, ?)
            """, (_ANNOUNCEMENT_USERNAME, int(time.time() * 1000), message)
        )
    flask.flash('Announcement posted.', 'success')
    return back_to('chat')


@admin_bp.route('/chat/delete/<int:msg_id>', methods=['POST'])
@admin_required
def route_delete_chat_message(msg_id: int) -> flask.Response:
    with database() as db:
        db.execute(
            """
            DELETE FROM chat_messages
            WHERE id = ?
            """, (msg_id,)
        )
    flask.flash('Chat message deleted.', 'success')
    return back_to('chat')


@admin_bp.route('/chat/edit/<int:msg_id>', methods=['POST'])
@admin_required
def route_edit_chat_message(msg_id: int) -> flask.Response:
    new_message = safe_str(flask.request.form.get('message', '').strip())

    if not new_message:
        flask.flash('Message cannot be empty.', 'error')
        return back_to('chat')

    with database() as db:
        db.execute(
            """
            UPDATE chat_messages
            SET message = ?
            WHERE id = ?
            """, (new_message, msg_id)
        )
    flask.flash('Chat message updated.', 'success')
    return back_to('chat')
