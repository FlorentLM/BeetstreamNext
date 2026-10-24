from __future__ import annotations

from typing import Callable
import flask

from beetsplug.beetstreamnext.blueprints import views
from beetsplug.beetstreamnext.blueprints.shared import route_registrar
from beetsplug.beetstreamnext.core.accounts import sessions as store


def register_session_routes(bp: flask.Blueprint, login_required: Callable) -> None:
    """
    Admin panel and account page expose the same web session routes,
    only the auth decorator differs.
    """

    route = route_registrar(bp, login_required)

    @route('/sessions', ['GET'])
    def list_sessions() -> str:
        return views.render_sessions(flask.session['username'], bp.name)

    @route('/sessions/<session_id>/revoke', ['POST'])
    def revoke_session(session_id: str) -> str:

        username = flask.session['username']

        if session_id != store.current_session_id():
            store.revoke_session(session_id, username)

        return views.render_sessions(username, bp.name)

    @route('/sessions/revoke-others', ['POST'])
    def revoke_other_sessions() -> str:

        username = flask.session['username']
        store.revoke_user_sessions(username, keep=store.current_session_id())

        return views.render_sessions(username, bp.name)
