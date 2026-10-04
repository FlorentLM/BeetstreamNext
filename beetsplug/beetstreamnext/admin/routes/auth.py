from __future__ import annotations
import hmac
import os
import flask

from .. import admin_bp

from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.utils.general import start_session
from beetsplug.beetstreamnext.core.tempstore import temporary_store
from beetsplug.beetstreamnext.core.users_crud import create_user, list_users
from beetsplug.beetstreamnext.forms import OnboardingForm, flash_form_errors


@admin_bp.route('/setup', methods=['GET', 'POST'])
def route_setup() -> flask.Response:

    if list_users():     # users exist: nothing to do here
        return flask.redirect(flask.url_for('auth.route_login'))

    form = OnboardingForm()
    if form.validate_on_submit():

        expected_key = os.environ.get('BEETSTREAMNEXT_KEY')

        if not expected_key or not hmac.compare_digest(form.setup_key.data, expected_key):
            flask.flash('Incorrect server key.', 'error')
            return flask.make_response(flask.render_template('setup.html', form=form))

        try:
            username = safe_str(form.username.data)
            raw_api_key = create_user(username, form.password.data, admin=True)
        except ValueError as e:
            flask.flash(str(e), 'error')

        else:
            token = temporary_store.put({'username': username, 'key': raw_api_key})

            # Auto-login into settings and show API key modal

            start_session(username, _api_key_token=token)

            return flask.redirect(flask.url_for('admin.route_settings'))

    elif form.is_submitted():
        flash_form_errors(form)

    return flask.make_response(flask.render_template('setup.html', form=form))
