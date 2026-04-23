import secrets
import flask

from beetsplug.beetstreamnext.application import app, rate_limiter, IP_filter
from beetsplug.beetstreamnext.maintenance import run_periodic
from beetsplug.beetstreamnext.users import load_user_roles, authenticate
from beetsplug.beetstreamnext.utils import grab_auth_params, subsonic_error, safe_str


@app.before_request
def _before_request():

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    if flask.request.path == '/':
        return

    if flask.request.path.rstrip('/') in ('/rest/getOpenSubsonicExtensions', '/rest/getOpenSubsonicExtensions.view'):
        return

    client_ip = str(flask.request.remote_addr) or 'unknown'

    if not IP_filter.is_allowed(client_ip):
        return subsonic_error(50, message='Access denied.', resp_fmt=resp_fmt)

    if rate_limiter.is_blocked(client_ip):
        return subsonic_error(40, message='Too many failed login attempts. Try again later.', resp_fmt=resp_fmt)

    # Attempt authentication
    ok, error_code, username = authenticate(r)
    if not ok:
        rate_limiter.record(client_ip)
        return subsonic_error(error_code, resp_fmt=resp_fmt)

    rate_limiter.reset(client_ip)

    flask.g.lib = app.config['lib']
    flask.g.username = username
    flask.g.user_data = load_user_roles(username)
    flask.g.playlist_provider = app.config['playlist_provider']
    flask.g._art_base_url = flask.url_for('api.endpoint_get_cover_art', _external=True, **grab_auth_params())

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
