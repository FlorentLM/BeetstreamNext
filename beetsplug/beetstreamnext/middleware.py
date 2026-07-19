import secrets
import flask

from beetsplug.beetstreamnext.constants import LOOPBACK_IPS
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.core.security import rate_limiter, ip_filter
from beetsplug.beetstreamnext.core.maintenance import run_periodic
from beetsplug.beetstreamnext.core.users_crud import load_user_roles, authenticate
from beetsplug.beetstreamnext.utils.general import grab_auth_params
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.api.responses import subsonic_error


@app.before_request
def _before_request() -> flask.Response | None:
    trusted_raw = app.config.get('trusted_hosts', '')
    if trusted_raw:
        allowed = {h.strip() for h in trusted_raw.split(',') if h.strip()}      # TODO: Better parser / validator

        raw_host = flask.request.host
        try:
            if raw_host.startswith('['):
                # [::1]:8080 -> ::1 (IPv6 literal, optional :port after ']')
                request_host = raw_host[1:raw_host.index(']')]
            else:
                # host:port or bare host -> take the host part
                request_host = raw_host.split(':')[0]
        except ValueError:
            flask.abort(400)

        if request_host not in allowed and request_host not in LOOPBACK_IPS:
            bsn_logger.warning(f'Blocking request with untrusted Host: {request_host}')
            flask.abort(403, description='Access Denied: Host not in TRUSTED_HOSTS.')

    r = flask.request.values
    is_api = flask.request.path.startswith('/rest')

    if is_api:
        resp_fmt = r.get('f', default='xml', type=safe_str)

    client_ip = flask.request.remote_addr or 'unknown'

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

    # Allow admin panel (auth is handled differently)
    if flask.request.path.startswith('/admin'):
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

    run_periodic()


@app.before_request
def _csp_nonce() -> None:
    if flask.request.path.startswith('/admin'):
        flask.g.csp_nonce = secrets.token_urlsafe(16)


@app.after_request
def _add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    if flask.request.is_secure:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    if flask.request.path.startswith('/admin'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        nonce = getattr(flask.g, 'csp_nonce', '')
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "      # no inline styles so only need 'self' and Google Fonts
            "font-src 'self' https://fonts.gstatic.com; "
            "style-src 'self' https://fonts.googleapis.com; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'; "
            "object-src 'none'"
        )
    return response