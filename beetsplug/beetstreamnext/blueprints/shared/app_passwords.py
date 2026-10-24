from __future__ import annotations

import time
from typing import Callable
import flask

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.blueprints import views
from beetsplug.beetstreamnext.blueprints.forms import AppPasswordForm, form_error_messages
from beetsplug.beetstreamnext.blueprints.shared import route_registrar
from beetsplug.beetstreamnext.core.accounts import app_passwords as store
from beetsplug.beetstreamnext.core.accounts.app_passwords import AppPasswordRole


def register_app_password_routes(bp: flask.Blueprint, login_required: Callable) -> None:
    """
    Admin panel and account page expose the same app password routes,
    only the auth decorator differs.
    """

    route = route_registrar(bp, login_required)

    @route('/app-passwords', ['GET'])
    def list_app_passwords() -> str:
        return views.render_app_passwords(flask.session['username'], bp.name)

    @route('/app-passwords', ['POST'])
    def create_app_password() -> str:

        username = flask.session['username']
        form = AppPasswordForm()

        if not form.validate_on_submit():
            return flask.render_template('partials/action_result.html', message=' '.join(form_error_messages(form)), ok=False)

        expires_at = time.time() + int(form.expires_days.data) * 86400 if form.expires_days.data else None

        as_password = bool(form.as_password.data) and app.config.get('legacy_auth', True)

        try:
            secret = store.create_app_password(username, form.name.data, AppPasswordRole(form.role.data), expires_at, as_password)
        except ValueError as e:
            return flask.render_template('partials/action_result.html', message=str(e), ok=False)

        modal = flask.render_template('partials/app_password_modal.html', secret=secret)
        app_pw_list = views.render_app_passwords(username, bp.name)

        partial = (
            f'<div id="appPasswordSlot" hx-swap-oob="innerHTML">{modal}</div>'
            f'<div id="appPasswordList" hx-swap-oob="innerHTML">{app_pw_list}</div>'
        )

        return partial

    @route('/app-passwords/<app_pwd_id>/revoke', ['POST'])
    def revoke_app_password(app_pwd_id: str) -> str:

        username = flask.session['username']

        if app_pwd_id != 'all':
            if not app_pwd_id.isdigit():
                flask.abort(404)
            app_pwd_id = int(app_pwd_id)

        store.revoke_app_password(username, app_pwd_id)

        return views.render_app_passwords(username, bp.name)
