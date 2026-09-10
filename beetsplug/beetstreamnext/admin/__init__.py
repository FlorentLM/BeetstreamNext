from functools import wraps
from typing import Callable
import flask
from flask import Blueprint

from beetsplug.beetstreamnext.core.users_crud import load_user_roles
from beetsplug.beetstreamnext.core.security import admin_host_allowed


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
        username = flask.session.get('username')

        if not username:
            return flask.redirect(flask.url_for('admin.route_login'))

        if not load_user_roles(username).get('adminRole', False):
            # Stale session (user deleted or demoted since login): drop
            flask.session.clear()
            flask.abort(403)
        return f(*args, **kwargs)

    return decorated


def back_to(anchor: str) -> flask.Response:
    return flask.redirect(flask.url_for('admin.route_settings') + f'#{anchor}')


from .routes import (
    auth,
    avatars,
    chat,
    jukebox,
    maintenance,
    podcasts_radio,
    security,
    settings,
    shares,
    users,
)