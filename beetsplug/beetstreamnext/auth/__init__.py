from __future__ import annotations

import hmac
import flask
from flask import Blueprint

from beetsplug.beetstreamnext.core.users_crud import get_user_roles, session_stamp
from beetsplug.beetstreamnext.core.security import admin_host_allowed

auth_bp = Blueprint('auth', __name__)


def can_login(roles: dict) -> bool:
    """Whether this user can sign in: admins and users with the settingsRole."""
    return bool(roles) and bool(roles.get('adminRole') or roles.get('settingsRole'))


def admin_here(roles: dict) -> bool:
    """Whether this user is an admin *and* admin panel is allowed on current host."""
    return bool(roles.get('adminRole')) and admin_host_allowed(flask.request.host)


def home_for(roles: dict) -> str:
    """Users -> normal account page. Admins -> admin panel if host is allowed, otherwise -> normal account page."""
    return flask.url_for('admin.route_settings' if admin_here(roles) else 'account.route_account')


def session_roles() -> dict | None:
    """
    Roles of the logged-in WebUI user, or None (clearing a stale session) if they may not be here.
    Re-checked on every request, so a user who lost their role (or was deleted), or whose password
    changed since this session started, is immediately blocked.
    """
    username = flask.session.get('username')
    if not username:
        return None

    roles = get_user_roles(username)
    stamp = flask.session.get('pv')
    if not can_login(roles) or not stamp or not hmac.compare_digest(stamp, session_stamp(username) or ''):
        flask.session.clear()
        return None

    return roles


from .routes import session
