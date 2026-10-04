from __future__ import annotations
import time
import flask

from .. import subsonic_bp

from beetsplug.beetstreamnext.constants import MAX_CHAT_CHAR
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.subsonic.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.utils.text import safe_str


# Spec: https://opensubsonic.netlify.app/docs/endpoints/addchatmessage/
@subsonic_bp.route('/addChatMessage', methods=['GET', 'POST'])
@subsonic_bp.route('/addChatMessage.view', methods=['GET', 'POST'])
def endpoint_add_chat_message() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    message = r.get('message', default='', type=safe_str)       # Required

    if not message:
        return subsonic_error(10, resp_fmt=resp_fmt)

    if len(message) > MAX_CHAT_CHAR:
        return subsonic_error(0, message=f'Message exceeds maximum length ({MAX_CHAT_CHAR} characters).', resp_fmt=resp_fmt)

    username = flask.g.username
    now_ms = int(time.time() * 1000)

    with database() as db:
        db.execute(
            """
            INSERT INTO chat_messages (username, time, message)
            VALUES (?, ?, ?)
            """, (username, now_ms, message)
        )

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getchatmessages/
@subsonic_bp.route('/getChatMessages', methods=['GET', 'POST'])
@subsonic_bp.route('/getChatMessages.view', methods=['GET', 'POST'])
def endpoint_get_chat_messages() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    since = r.get('since', default=0, type=int)

    with database() as db:
        query = "SELECT username, time, message FROM chat_messages"
        params = []

        if since > 0:
            query += " WHERE time > ?"
            params.append(since)

        # only newest 100 messages to prevent massive payloads
        query += " ORDER BY time DESC LIMIT 100"

        rows = db.execute(query, params).fetchall()

    # clients expect the array to be chronological?
    messages = []
    for row in reversed(rows):
        messages.append({
            'username': row['username'],
            'time': int(row['time']),
            'message': row['message']
        })

    payload = {
        'chatMessages': {
            'chatMessage': messages
        }
    }

    return subsonic_response(payload, resp_fmt=resp_fmt)