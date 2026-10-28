from __future__ import annotations

from functools import wraps
from typing import Callable
import flask
from flask import Blueprint

from beetsplug.beetstreamnext.blueprints.auth import session_roles
from beetsplug.beetstreamnext.blueprints.shared.radio import register_radio_routes
from beetsplug.beetstreamnext.blueprints.shared.shares import register_share_routes
from beetsplug.beetstreamnext.blueprints.shared.app_passwords import register_app_password_routes
from beetsplug.beetstreamnext.blueprints.shared.sessions import register_session_routes
from beetsplug.beetstreamnext.blueprints.shared.chat import register_chat_routes


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required(f) -> Callable:
    """Decorator: redirect to login if the session has no valid admin user."""
    @wraps(f)
    def decorated(*args, **kwargs) -> flask.Response:
        roles = session_roles()
        if roles is None:
            return flask.redirect(flask.url_for('auth.route_login'))

        if not roles.get('adminRole'):
            return flask.redirect(flask.url_for('account.route_account'))
        return f(*args, **kwargs)

    decorated.requires_auth = True
    return decorated


def back_to(anchor: str) -> flask.Response:
    return flask.redirect(flask.url_for('admin.route_settings') + f'#{anchor}')


from .routes import (
    artists,
    auth,
    beets,
    events,
    jukebox,
    maintenance,
    notifications,
    podcasts,
    security,
    settings,
    shares,
    users,
)

# Admin panel and account page expose the same routes, only the auth decorator differs
register_radio_routes(admin_bp, admin_required)
register_share_routes(admin_bp, admin_required, owner_only=False)
register_app_password_routes(admin_bp, admin_required)
register_session_routes(admin_bp, admin_required)
register_chat_routes(admin_bp, admin_required, moderator=True)
