from functools import wraps
from typing import Callable
import flask
from flask import Blueprint

from beetsplug.beetstreamnext.core.users_crud import load_user_roles


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

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