from __future__ import annotations

import hmac
from pathlib import Path

import flask
from flask import Blueprint

from beetsplug.beetstreamnext.core.accounts.security import rate_limiter
from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.core.library.beets.interaction import import_session
from beetsplug.beetstreamnext.core.library.import_paths import validate_pinned_path
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.utils.system import safe_join

control_api_bp = Blueprint('control_api', __name__, url_prefix='/control-api')


def _json(status: int, **body) -> flask.Response:
    response = flask.jsonify(ok=status < 300, **body)
    response.status_code = status
    return response


@control_api_bp.route('/trigger-import', methods=['POST'])
def route_trigger_import() -> flask.Response:
    """
    Trigger a beets import on command (when a download completes, called by download client).

    Fields:
        - 'path': folder to import
        - 'torrent_name' (optional): for clients that give the download directory and the torrent name separately
        - 'interactive': true/false (defaults to false -> quiet import)
    Auth:
        - `Authorization: Bearer <token>`
        - `X-Api-Key` header
        - `token` form field
    """

    client_ip = flask.request.remote_addr or 'unknown'

    expected = settings_store.get('control_api_token')
    if not expected:
        flask.abort(404)

    auth = flask.request.headers.get('Authorization', '')
    if auth.lower().startswith('bearer '):
        received = auth[7:].strip()
    else:
        received = (flask.request.headers.get('X-Api-Key') or flask.request.form.get('token') or '').strip()

    if not hmac.compare_digest(received.encode(), str(expected).encode()):
        rate_limiter.record(client_ip, 'control-api')
        return _json(401, message='Invalid import token.')

    rate_limiter.reset(client_ip, 'control-api')

    path, torrent_name = (flask.request.form.get(k, '').strip() for k in ('path', 'torrent_name'))
    if not path:
        return _json(400, message="Provide 'path'.")

    try:
        req_path = str(safe_join(Path(path), torrent_name)) if torrent_name else path
    except ValueError:
        return _json(400, message="'torrent_name' must stay inside 'path'.")

    interactive = flask.request.form.get('interactive', default=False, type=api_bool)

    error = validate_pinned_path(req_path)
    if error:
        return _json(400, message=error)

    ok, message = import_session.enqueue(req_path, quiet=not interactive)
    if not ok:
        return _json(409, message=message)

    bsn_logger.info(f"Control API: '{req_path}' ({'interactive' if interactive else 'quiet'}): {message}")

    return _json(202, message=message, **import_session.status())
