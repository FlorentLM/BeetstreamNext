from __future__ import annotations

import time
from collections.abc import Callable
import flask

from beetsplug.beetstreamnext.blueprints.forms import ConnectionURLTokenForm, form_error_messages
from beetsplug.beetstreamnext.blueprints.shared import route_registrar
from beetsplug.beetstreamnext.core.config.validators import validate_http_url
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.scrobbling import SCROBBLERS, service as scrobbling
from beetsplug.beetstreamnext.core.services.scrobbling.base import ScrobbleError
from beetsplug.beetstreamnext.utils.concurrency import RollingLimiter


connect_limiter = RollingLimiter(5, 60)

TOKEN_MASK = '\u2022' * 12
PENDING_KEY = 'scrobbling_pending'
PENDING_TTL_SEC = 3600      # Last.fm tokens are valid for 1 hour


def register_scrobbling_routes(bp: flask.Blueprint, login_required: Callable) -> None:
    """Same routes on the admin panel and the account page, only the auth decorator differs."""

    route = route_registrar(bp, login_required)

    def pending_auths() -> dict[str, str]:
        """Web auth started (but not finished): service -> URL to approve it."""
        pending = flask.session.get(PENDING_KEY) or {}
        urls = {}
        for service, entry in pending.items():
            scrobbler = SCROBBLERS.get(service)
            if scrobbler and time.time() - entry['at'] < PENDING_TTL_SEC:
                urls[service] = scrobbler.auth_page(entry['token'])
        return urls

    def card(message: str | None = None, ok: bool = True, opened: str = '') -> str:
        username = flask.session['username']
        return flask.render_template(
            'partials/scrobbling.html',
            bp_name=bp.name,
            enabled=scrobbling.is_allowed(username),
            scrobblers=SCROBBLERS.values(),
            existing={c['service']: c for c in scrobbling.list_connections(username)},
            token_form=ConnectionURLTokenForm(formdata=None),
            token_mask=TOKEN_MASK,
            pending=pending_auths(),
            message=message,
            ok=ok,
            opened=opened,
        )

    @route('/scrobbling', ['GET'])
    def show_scrobbling() -> str:
        return card()

    @route('/scrobbling/<service>/connect', ['POST'])
    def connect_service(service: str) -> str:

        username = flask.session['username']
        scrobbler = SCROBBLERS.get(service)
        if scrobbler is None or scrobbler.auth_kind != 'token':
            flask.abort(404)

        form = ConnectionURLTokenForm()
        if not scrobbling.is_allowed(username):
            return card(opened=service)
        if not form.validate_on_submit():
            return card(' '.join(form_error_messages(form)), ok=False, opened=service)
        if not connect_limiter.allow(username):
            return card('Too many attempts, try again in a minute.', ok=False, opened=service)

        try:
            url = validate_http_url(form.url.data)
        except ValueError as e:
            return card(str(e), ok=False, opened=service)

        # Keep saved token (so URL can be changed by itself)
        token = form.token.data.strip().removeprefix(TOKEN_MASK).strip()
        if not token:
            token = scrobbling.get_credentials(username, service).get('token', '')
        if not token:
            return card('Paste your token.', ok=False, opened=service)

        credentials = {'token': token, 'url': url}

        try:
            display_name = scrobbler.verify(credentials)
        except ScrobbleError as e:
            return card(str(e), ok=False, opened=service)

        scrobbling.save_connection(username, service, credentials, display_name)
        bsn_logger.info(f"'{username}' connected {scrobbler.label}")

        return card(f'Connected to {scrobbler.label}' + (f' as {display_name}.' if display_name else '.'), opened=service)

    def web_scrobbler(service: str):
        scrobbler = SCROBBLERS.get(service)
        if scrobbler is None or scrobbler.auth_kind != 'webauth':
            flask.abort(404)
        return scrobbler

    @route('/scrobbling/<service>/authorize', ['POST'])
    def authorize_service(service: str) -> str:
        """Web auth step 1: get token and show the link to approve it."""

        username = flask.session['username']
        scrobbler = web_scrobbler(service)

        if not scrobbling.is_allowed(username):
            return card(opened=service)
        if not scrobbler.available():
            return card(f'{scrobbler.label} needs an API key and shared secret, ask an admin to set them.', ok=False, opened=service)
        if not connect_limiter.allow(username):
            return card('Too many attempts, try again in a minute.', ok=False, opened=service)

        try:
            token, _ = scrobbler.start_auth()
        except ScrobbleError as e:
            return card(str(e), ok=False, opened=service)

        pending = dict(flask.session.get(PENDING_KEY) or {})
        pending[service] = {'token': token, 'at': time.time()}
        flask.session[PENDING_KEY] = pending
        return card(opened=service)

    @route('/scrobbling/<service>/finish', ['POST'])
    def finish_service(service: str) -> str:
        """Web auth step 2: user says they approved, exchange the token for a session key."""

        username = flask.session['username']
        scrobbler = web_scrobbler(service)

        entry = (flask.session.get(PENDING_KEY) or {}).get(service)
        if not scrobbling.is_allowed(username):
            return card(opened=service)
        if not entry or time.time() - entry['at'] >= PENDING_TTL_SEC:
            return card('The authorisation expired, start again.', ok=False, opened=service)
        if not connect_limiter.allow(username):
            return card('Too many attempts, try again in a minute.', ok=False, opened=service)

        try:
            credentials, display_name = scrobbler.finish_auth(entry['token'])
        except ScrobbleError as e:
            return card(str(e), ok=False, opened=service)     # Keep pending token until approval

        pending = dict(flask.session.get(PENDING_KEY) or {})
        pending.pop(service, None)
        flask.session[PENDING_KEY] = pending

        scrobbling.save_connection(username, service, credentials, display_name)
        bsn_logger.info(f"'{username}' connected {scrobbler.label}")

        return card(f'Connected to {scrobbler.label}' + (f' as {display_name}.' if display_name else '.'), opened=service)

    @route('/scrobbling/<service>/cancel', ['POST'])
    def cancel_service(service: str) -> str:
        """Forgets a web auth that is waiting for approval."""

        web_scrobbler(service)
        pending = dict(flask.session.get(PENDING_KEY) or {})
        pending.pop(service, None)
        flask.session[PENDING_KEY] = pending
        return card(opened=service)

    @route('/scrobbling/<service>/enabled', ['POST'])
    def toggle_service(service: str) -> str:

        if service not in SCROBBLERS:
            flask.abort(404)

        enabled = flask.request.form.get('enabled') == '1'
        scrobbling.set_enabled(flask.session['username'], service, enabled)
        return ''

    @route('/scrobbling/<service>/disconnect', ['POST'])
    def disconnect_service(service: str) -> str:

        if service not in SCROBBLERS:
            flask.abort(404)

        scrobbling.delete_connection(flask.session['username'], service)
        return card('Disconnected.', opened=service)
