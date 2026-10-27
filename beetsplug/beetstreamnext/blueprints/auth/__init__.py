from __future__ import annotations

import hmac
import flask
from flask import Blueprint

from beetsplug.beetstreamnext.core.accounts.sessions import validate_session
from beetsplug.beetstreamnext.core.accounts.users_crud import get_user_roles, session_stamp
from beetsplug.beetstreamnext.constants import Scope
from beetsplug.beetstreamnext.core.runtime.listeners import ListenerScope

auth_bp = Blueprint('auth', __name__)


def can_login(roles: dict) -> bool:
    """Whether this user can sign in: admins and users with the settingsRole."""
    return bool(roles) and bool(roles.get('adminRole') or roles.get('settingsRole'))


def admin_here(roles: dict) -> bool:
    """Whether this user is an admin *and* the listener they are on serves the admin panel."""
    return bool(roles.get('adminRole')) and flask.request.environ.get(ListenerScope.KEY, Scope.ADMIN) is Scope.ADMIN


def home_for(roles: dict) -> str:
    """Users -> normal account page. Admins -> admin panel if this listener serves it, otherwise -> normal account page."""
    return flask.url_for('admin.route_settings' if admin_here(roles) else 'account.route_account')


def session_roles() -> dict | None:
    """
    Roles of the logged-in WebUI user, or None (clearing a stale session) if they may not be here.
    Re-checked on every request so a user who shouldn't access anymore is immediately blocked.
    """
    username = flask.session.get('username')
    if not username:
        return None

    roles = get_user_roles(username)
    stamp = flask.session.get('pv')
    if (not can_login(roles) or not stamp or not hmac.compare_digest(stamp, session_stamp(username) or '')\
            or not validate_session(flask.session.get('sid'), username)):
        flask.session.clear()
        return None

    return roles


from .routes import session
