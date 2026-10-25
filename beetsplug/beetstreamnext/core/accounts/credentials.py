from __future__ import annotations

from typing import Optional, Tuple
import flask

from beetsplug.beetstreamnext.core.accounts.passwords import verify_password
from beetsplug.beetstreamnext.core.accounts.sessions import create_session
from beetsplug.beetstreamnext.core.accounts.users_crud import get_password_hash, session_stamp


def check_password(username: str, password: str) -> bool:
    """Check an account password. Only used for the WebUI: Subsonic clients use app passwords."""

    stored = get_password_hash(username)

    if stored is None:
        verify_password(password=password, stored=None)
        return False

    return verify_password(password=password, stored=stored)


def webui_login(username: str, password: str) -> Tuple[bool, Optional[str]]:
    """
    Password check for the WebUI's login form.
    """
    if check_password(username, password):
        return True, username

    return False, None


def start_session(username: str, **extra) -> None:
    """Login `username` in the WebUI."""
    flask.session.clear()   # prevent session fixation
    flask.session.permanent = True
    flask.session['username'] = username
    flask.session['pv'] = session_stamp(username)
    flask.session['sid'] = create_session(
        username, ip=flask.request.remote_addr, user_agent=flask.request.headers.get('User-Agent')
    )
    flask.session.update(extra)
