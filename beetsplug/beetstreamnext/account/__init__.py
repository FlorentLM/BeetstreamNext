from __future__ import annotations

from functools import wraps
from typing import Callable
import flask
from flask import Blueprint

from beetsplug.beetstreamnext.auth import session_roles, admin_here


account_bp = Blueprint('account', __name__, url_prefix='/account')


def account_required(f) -> Callable:
    """Decorator: redirect to login unless session belongs to a user allowed to manage their account."""
    @wraps(f)
    def decorated(*args, **kwargs) -> flask.Response:
        roles = session_roles()
        if roles is None:
            return flask.redirect(flask.url_for('auth.route_login'))

        if admin_here(roles):
            return flask.redirect(flask.url_for('admin.route_settings'))

        flask.g.account_user = flask.session['username']
        return f(*args, **kwargs)

    return decorated


from .routes import (
    profile,
    lists,
)
