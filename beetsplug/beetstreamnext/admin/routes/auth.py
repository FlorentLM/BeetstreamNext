import flask
from werkzeug.datastructures import MultiDict

from .. import admin_bp

from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.core.security import ip_filter, rate_limiter
from beetsplug.beetstreamnext.core.users_crud import load_user_roles, authenticate
from beetsplug.beetstreamnext.admin.forms import LoginForm


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

        # TODO: Actually the blocked and rate-limited checks are not needed since before_request does them anyway...
        blocked = False
        if not ip_filter.is_allowed(client_ip):
            blocked = True
            flask.flash('Access denied.', 'error')

        if rate_limiter.is_blocked(client_ip):
            blocked = True
            flask.flash('Too many failed login attempts. Try again later.', 'error')

        if not blocked:
            # Build auth dict (simulating a Subsonic API request)
            auth_params = MultiDict(
                {'u': safe_str(form.username.data),
                 'p': form.password.data}
            )
            ok, _, username = authenticate(auth_params)
            if ok and load_user_roles(username).get('adminRole', False):
                # Success. Clear failures for this IP and establish session
                rate_limiter.reset(client_ip)
                flask.session.clear()  # prevent session fixation
                flask.session.permanent = True
                flask.session['username'] = username
                return flask.redirect(flask.url_for('admin.route_settings'))

            # Same generic message whether password was wrong or user isn't an admin
            rate_limiter.record(client_ip)
            flask.flash('Invalid credentials.', 'error')

    return flask.make_response(flask.render_template('login.html', form=form))


@admin_bp.route('/logout', methods=['POST'])
def route_logout() -> flask.Response:
    flask.session.clear()
    return flask.redirect(flask.url_for('admin.route_login'))
