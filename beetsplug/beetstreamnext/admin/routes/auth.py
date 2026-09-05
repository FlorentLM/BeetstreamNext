import hmac
import os
import flask
from werkzeug.datastructures import MultiDict

from .. import admin_bp

from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.core.security import rate_limiter
from beetsplug.beetstreamnext.core.tempstore import temporary_store
from beetsplug.beetstreamnext.core.users_crud import create_user, load_all_users, load_user_roles, authenticate
from beetsplug.beetstreamnext.admin.forms import LoginForm, OnboardingForm


@admin_bp.route('/setup', methods=['GET', 'POST'])
def route_setup() -> flask.Response:

    if load_all_users():     # users exist: nothing to do here
        return flask.redirect(flask.url_for('admin.route_login'))

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

            flask.session.clear()   # prevent session fixation
            flask.session.permanent = True
            flask.session['username'] = username
            flask.session['_api_key_token'] = token

            return flask.redirect(flask.url_for('admin.route_settings'))

    elif form.is_submitted():
        for field_name, errors in form.errors.items():
            for error in errors:
                msg = error if field_name == 'confirm_password' else f'{field_name}: {error}'
                flask.flash(msg, 'error')

    return flask.make_response(flask.render_template('setup.html', form=form))


@admin_bp.route('/login', methods=['GET', 'POST'])
def route_login() -> flask.Response:

    # Skip login page if the existing session belongs to an admin
    session_user = flask.session.get('username')
    if session_user:
        if load_user_roles(session_user).get('adminRole', False):
            return flask.redirect(flask.url_for('admin.route_settings'))
        flask.session.clear()

    form = LoginForm()
    if form.validate_on_submit():
        client_ip = flask.request.remote_addr or 'unknown'
        attempted_user = safe_str(form.username.data)

        # ip_filter / rate_limiter run in before_request, but still record/reset failures here.

        # Build auth dict (simulating a Subsonic API request)
        auth_params = MultiDict(
            {'u': attempted_user,
             'p': form.password.data}
        )
        ok, _, username = authenticate(auth_params)
        if ok and load_user_roles(username).get('adminRole', False):
            # Success. Clear failures for this IP and establish session
            rate_limiter.reset(client_ip, attempted_user)
            flask.session.clear()  # prevent session fixation
            flask.session.permanent = True
            flask.session['username'] = username
            return flask.redirect(flask.url_for('admin.route_settings'))

        # Same generic message whether password was wrong or user isn't an admin
        rate_limiter.record(client_ip, attempted_user)
        flask.flash('Invalid credentials.', 'error')

    return flask.make_response(flask.render_template('login.html', form=form))


@admin_bp.route('/logout', methods=['POST'])
def route_logout() -> flask.Response:
    flask.session.clear()
    return flask.redirect(flask.url_for('admin.route_login'))
