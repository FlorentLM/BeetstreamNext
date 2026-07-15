import secrets
import flask

from .application import app
from beetsplug.beetstreamnext.core.security import rate_limiter, ip_filter
from beetsplug.beetstreamnext.core.maintenance import run_periodic
from beetsplug.beetstreamnext.core.users_crud import load_user_roles, authenticate
from beetsplug.beetstreamnext.utils.general import grab_auth_params
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.api.responses import subsonic_error


@app.before_request
def _before_request():
    r = flask.request.values
    is_api = flask.request.path.startswith('/rest')

    if is_api:
        resp_fmt = r.get('f', default='xml', type=safe_str)

    client_ip = str(flask.request.remote_addr) or 'unknown'

    if not ip_filter.is_allowed(client_ip):
        if is_api:
            return subsonic_error(50, message='Access denied.', resp_fmt=resp_fmt)
        flask.abort(403)

    if rate_limiter.is_blocked(client_ip):
        if is_api:
            return subsonic_error(40, message='Too many failed login attempts. Try again later.', resp_fmt=resp_fmt)
        flask.abort(429)

    # Allow public homepage
    if flask.request.path == '/':
        return

    # Allow these two rest endpoints as per OpenSubsonic spec
    if flask.request.path.rstrip('/') in ('/rest/getOpenSubsonicExtensions', '/rest/getOpenSubsonicExtensions.view'):
        return

    # Allow static content
    if flask.request.path.startswith('/static'):
        return

    # Unknown path: raise 404
    if flask.request.url_rule is None:
        return


    # Attempt authentication
    ok, error_code, username = authenticate(r)
    if not ok:
        rate_limiter.record(client_ip)
        if is_api:
            return subsonic_error(error_code, resp_fmt=resp_fmt)
        flask.abort(401)

    rate_limiter.reset(client_ip)

    flask.g.lib = app.config['lib']
    flask.g.username = username
    flask.g.user_data = load_user_roles(username)
    flask.g.playlist_provider = app.config['playlist_provider']
    flask.g._art_base_url = flask.url_for('api.endpoint_get_cover_art', _external=True, **grab_auth_params())

    # just to be sure
    if flask.request.is_secure and not app.config.get('SESSION_COOKIE_SECURE', False):
        app.config.update(SESSION_COOKIE_SECURE=True)

    run_periodic()


@app.before_request
def _csp_nonce():
    if flask.request.path.startswith('/admin'):
        flask.g.csp_nonce = secrets.token_urlsafe(16)


@app.after_request
def _add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    if flask.request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    return response