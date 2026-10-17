from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
import flask

from .. import subsonic_bp

from beetsplug.beetstreamnext.constants import (
    CHAT_MAX_STORED, CHAT_RATE_MAX, CHAT_RATE_WINDOW_SEC, CHAT_RETENTION_SEC, CHAT_MAX_CHARS
)
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.blueprints.subsonic.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.utils.text import safe_str


_recent_posts: dict[str, deque] = defaultdict(deque)     # username -> times of latest messages
_recent_posts_lock = threading.Lock()


def post_message(username: str, now: float) -> bool:

    with _recent_posts_lock:
        posts = _recent_posts[username]
        while posts and now - posts[0] > CHAT_RATE_WINDOW_SEC:
            posts.popleft()

        if len(posts) >= CHAT_RATE_MAX:
            return False

        posts.append(now)
        return True


# Spec: https://opensubsonic.netlify.app/docs/endpoints/addchatmessage/
@subsonic_bp.route('/addChatMessage', methods=['GET', 'POST'])
@subsonic_bp.route('/addChatMessage.view', methods=['GET', 'POST'])
def endpoint_add_chat_message() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    message = r.get('message', default='', type=safe_str)       # Required

    if not message:
        return subsonic_error(10, resp_fmt=resp_fmt)

    if len(message) > CHAT_MAX_CHARS:
        return subsonic_error(0, message=f'Message exceeds maximum length ({CHAT_MAX_CHARS} characters).', resp_fmt=resp_fmt)

    username = flask.g.username
    now = time.time()
    now_ms = int(now * 1000)

    if not post_message(username, now):
        return subsonic_error(0, message='Too many messages, slow down.', resp_fmt=resp_fmt)

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

        # Drop any messages if over 2000 / user
        db.execute(
            """
            DELETE FROM chat_messages
            WHERE id NOT IN (SELECT id FROM chat_messages ORDER BY time DESC, id DESC LIMIT ?)
            """, (CHAT_MAX_STORED,)
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