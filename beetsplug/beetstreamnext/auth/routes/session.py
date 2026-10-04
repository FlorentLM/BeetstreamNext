from __future__ import annotations

import flask

from .. import auth_bp

from beetsplug.beetstreamnext.auth import can_login, home_for, session_roles
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.utils.general import start_session
from beetsplug.beetstreamnext.core.accounts.security import rate_limiter
from beetsplug.beetstreamnext.auth.credentials import webui_login
from beetsplug.beetstreamnext.core.accounts.users_crud import get_user_roles
from beetsplug.beetstreamnext.forms import LoginForm


@auth_bp.route('/login', methods=['GET', 'POST'])
def route_login() -> flask.Response:

    roles = session_roles()
    if roles:
        return flask.redirect(home_for(roles))

    form = LoginForm()
    if form.validate_on_submit():
        client_ip = flask.request.remote_addr or 'unknown'
        attempted_user = safe_str(form.username.data)

        ok, username = webui_login(attempted_user, form.password.data)

        # Users without the settings role are blocked here
        roles = get_user_roles(username) if ok else {}

        if ok and can_login(roles):
            rate_limiter.reset(client_ip, attempted_user)

            start_session(username)
            return flask.redirect(home_for(roles))

        rate_limiter.record(client_ip, attempted_user)
        flask.flash('Invalid credentials.', 'error')

    return flask.make_response(flask.render_template('login.html', form=form))


@auth_bp.route('/logout', methods=['POST'])
def route_logout() -> flask.Response:
    flask.session.clear()
    return flask.redirect(flask.url_for('public.home'))
