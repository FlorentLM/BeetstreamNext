from __future__ import annotations

from typing import Callable
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


def register_scrobbling_routes(bp: flask.Blueprint, login_required: Callable) -> None:
    """Same routes on the admin panel and the account page, only the auth decorator differs."""

    route = route_registrar(bp, login_required)

    def card(message: str | None = None, ok: bool = True) -> str:
        username = flask.session['username']
        return flask.render_template(
            'partials/scrobbling.html',
            bp_name=bp.name,
            enabled=scrobbling.is_allowed(username),
            scrobblers=SCROBBLERS.values(),
            existing={c['service']: c for c in scrobbling.list_connections(username)},
            token_form=ConnectionURLTokenForm(formdata=None),
            token_mask=TOKEN_MASK,
            message=message,
            ok=ok,
        )

    @route('/scrobbling', ['GET'])
    def show_scrobbling() -> str:
        return card()

    @route('/scrobbling/<service>/connect', ['POST'])
    def connect_service(service: str) -> str:

        username = flask.session['username']
        scrobbler = SCROBBLERS.get(service)
        if scrobbler is None:
            flask.abort(404)

        form = ConnectionURLTokenForm()
        if not scrobbling.is_allowed(username):
            return card()
        if not form.validate_on_submit():
            return card(' '.join(form_error_messages(form)), ok=False)
        if not connect_limiter.allow(username):
            return card('Too many attempts, try again in a minute.', ok=False)

        try:
            url = validate_http_url(form.url.data)
        except ValueError as e:
            return card(str(e), ok=False)

        # Keep saved token (so URL can be changed by itself)
        token = form.token.data.strip().removeprefix(TOKEN_MASK).strip()
        if not token:
            token = scrobbling.get_credentials(username, service).get('token', '')
        if not token:
            return card('Paste your token.', ok=False)

        credentials = {'token': token, 'url': url}

        try:
            display_name = scrobbler.verify(credentials)
        except ScrobbleError as e:
            return card(str(e), ok=False)

        scrobbling.save_connection(username, service, credentials, display_name)
        bsn_logger.info(f"'{username}' connected {scrobbler.label}")

        return card(f'Connected to {scrobbler.label}' + (f' as {display_name}.' if display_name else '.'))

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
        return card('Disconnected.')
