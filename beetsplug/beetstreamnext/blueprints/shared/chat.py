from __future__ import annotations

from typing import Callable, Optional
import flask

from beetsplug.beetstreamnext.blueprints.shared import route_registrar
from beetsplug.beetstreamnext.constants import CHAT_MAX_CHARS
from beetsplug.beetstreamnext.core.media.avatars import avatar_response
from beetsplug.beetstreamnext.core.services import chat
from beetsplug.beetstreamnext.core.services.chat import ChatError
from beetsplug.beetstreamnext.utils.text import safe_str


def register_chat_routes(bp: flask.Blueprint, login_required: Callable, moderator: bool = False) -> None:
    """
    Admin panel and account page share the chat panel and its routes.
    A moderator can post as the Server (or as themselves) and edit / delete any message.
    """
    bp_name = bp.name
    route = route_registrar(bp, login_required)

    def render(template: str, **context) -> str:
        return flask.render_template(
            template, bp_name=bp_name, moderator=moderator, me=flask.session['username'],
            CHAT_MAX_CHARS=CHAT_MAX_CHARS, **context)

    def render_message(msg_id: int, template: str = 'partials/chat_message.html', **context) -> str:
        message = chat.get_message(msg_id)
        if not message:
            flask.abort(404)
        return render(template, m=message, **context)

    @route('/chat', ['GET'])
    def chat_panel() -> str:
        messages, has_older = chat.fetch_messages()
        return render('partials/chat_panel.html', messages=messages, has_older=has_older)

    @route('/chat/messages', ['GET'])
    def chat_messages() -> str:
        after: Optional[int] = flask.request.args.get('after', type=int)
        before: Optional[int] = flask.request.args.get('before', type=int)

        messages, has_older = chat.fetch_messages(after_id=after, before_id=before)
        return render('partials/chat_messages.html', messages=messages, has_older=has_older,
                      mode='after' if after is not None else 'before')

    @route('/chat/avatar/<username>', ['GET'])
    def chat_avatar(username: str) -> flask.Response:
        return avatar_response(username)

    @route('/chat/send', ['POST'])
    def send_chat_message() -> flask.Response | str:
        as_server = moderator and flask.request.form.get('as_server') == '1'
        username = 'Server' if as_server else flask.session['username']

        try:
            chat.post_message(username, flask.request.form.get('message', ''))
        except ChatError as e:
            return render('partials/action_result.html', message=str(e), ok=False)

        resp = flask.make_response('', 204)
        resp.headers['HX-Trigger'] = 'bsn-chat-refresh'
        return resp

    if not moderator:
        return

    @route('/chat/<int:msg_id>', ['GET'])
    def chat_message(msg_id: int) -> str:
        return render_message(msg_id)

    @route('/chat/<int:msg_id>/edit', ['GET'])
    def chat_edit_form(msg_id: int) -> str:
        return render_message(msg_id, 'partials/chat_edit_form.html')

    @route('/chat/<int:msg_id>/edit', ['POST'])
    def edit_chat_message(msg_id: int) -> str:
        try:
            if not chat.edit_message(msg_id, flask.request.form.get('message', '')):
                flask.abort(404)

        except ChatError as e:
            return render_message(msg_id, 'partials/chat_edit_form.html', error=str(e),
                                  draft=safe_str(flask.request.form.get('message', '')))

        return render_message(msg_id)

    @route('/chat/<int:msg_id>/delete', ['POST'])
    def delete_chat_message(msg_id: int) -> str:
        chat.delete_message(msg_id)
        return ''
