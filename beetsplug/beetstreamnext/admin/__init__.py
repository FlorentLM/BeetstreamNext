from functools import wraps
from typing import Callable
import flask
from flask import Blueprint

from beetsplug.beetstreamnext.core.users_crud import load_user_roles


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.before_request
def restrict_admin_host() -> None:
    """Enforce internal-only hostname rules if admin_hostname is configured."""
    from beetsplug.beetstreamnext.settings import settings_store
    from beetsplug.beetstreamnext.constants import LOOPBACK_IPS

    admin_host = settings_store.get('admin_hostname')
    if admin_host:
        raw_host = flask.request.host
        try:
            if raw_host.startswith('['):
                request_host = raw_host[1:raw_host.index(']')]
            else:
                request_host = raw_host.split(':')[0]
        except ValueError:
            flask.abort(400)

        # Allow loopback/localhost and the specified admin host
        if request_host != admin_host and request_host not in LOOPBACK_IPS:
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


from .routes import (
    auth,
    avatars,
    settings_routes,
    users,
)