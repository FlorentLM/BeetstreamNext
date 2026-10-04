from __future__ import annotations

from functools import wraps
from typing import Callable
import flask
from flask import Blueprint

from beetsplug.beetstreamnext.blueprints.auth import session_roles
from beetsplug.beetstreamnext.core.accounts.security import admin_host_allowed
from beetsplug.beetstreamnext.blueprints.shared.radio import register_radio_routes


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.before_request
def restrict_admin_host() -> None:
    """Enforce internal-only hostname rules if admin_hostname is configured."""
    if not admin_host_allowed(flask.request.host):
        flask.abort(403, description='Admin panel access denied on this hostname.')


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

    return decorated


def back_to(anchor: str) -> flask.Response:
    return flask.redirect(flask.url_for('admin.route_settings') + f'#{anchor}')


from .routes import (
    artists,
    auth,
    beets,
    chat,
    events,
    jukebox,
    maintenance,
    podcasts,
    security,
    settings,
    shares,
    users,
)

# Admin panel and account page expose the same routes, only the auth decorator differs
register_radio_routes(admin_bp, admin_required)
