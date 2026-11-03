from __future__ import annotations
import hmac
import os
import flask

from .. import admin_bp

from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.core.accounts.credentials import start_session
from beetsplug.beetstreamnext.core.accounts.security import rate_limiter
from beetsplug.beetstreamnext.core.accounts.users_crud import create_user, list_users
from beetsplug.beetstreamnext.blueprints.forms import OnboardingForm, flash_form_errors


@admin_bp.route('/setup', methods=['GET', 'POST'])
def route_setup() -> flask.Response:

    if list_users():     # users exist: nothing to do here
        return flask.redirect(flask.url_for('auth.route_login'))

    form = OnboardingForm()
    if form.validate_on_submit():

        expected_key = os.environ.get('BEETSTREAMNEXT_KEY')
        client_ip = flask.request.remote_addr or 'unknown'

        if not expected_key or not hmac.compare_digest(form.setup_key.data.encode(), expected_key.encode()):
            rate_limiter.record(client_ip)
            flask.flash('Incorrect server key.', 'error')
            return flask.make_response(flask.render_template('setup.html', form=form))

        rate_limiter.reset(client_ip)

        try:
            username = safe_str(form.username.data)
            create_user(username, form.password.data, admin=True)
        except ValueError as e:
            flask.flash(str(e), 'error')

        else:
            # Auto-login into settings
            start_session(username)

            return flask.redirect(flask.url_for('admin.route_settings'))

    elif form.is_submitted():
        flash_form_errors(form)

    return flask.make_response(flask.render_template('setup.html', form=form))
