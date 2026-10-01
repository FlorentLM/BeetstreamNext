from __future__ import annotations

import os
from pathlib import Path
import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.beets_interaction import htmlify_log, start_import, import_status, send_import_input, read_config


@admin_bp.route('/beets/import-log', methods=['GET'])
@admin_required
def route_beets_import_log() -> flask.Response:
    return flask.jsonify({'lines': htmlify_log()})


@admin_bp.route('/beets/config', methods=['GET'])
@admin_required
def route_read_beets_config() -> flask.Response:
    return flask.render_template('partials/beets_config.html', beets_config=read_config())


@admin_bp.route('/beets/import/start', methods=['POST'])
@admin_required
def route_beets_import_start() -> flask.Response:
    path = (flask.request.form.get('path') or '').strip()
    ok, message = start_import(path)
    status = import_status()
    return flask.jsonify({'ok': ok, 'message': message, **status})


@admin_bp.route('/beets/import/status', methods=['GET'])
@admin_required
def route_beets_import_status() -> flask.Response:
    return flask.jsonify(import_status())


@admin_bp.route('/beets/import/input', methods=['POST'])
@admin_required
def route_beets_import_input() -> flask.Response:
    text = flask.request.form.get('text', '')
    ok, message = send_import_input(text)
    return flask.jsonify({'ok': ok, 'message': message, **import_status()})


@admin_bp.route('/beets/browse', methods=['GET'])
@admin_required
def route_beets_import_browse() -> flask.Response:

    raw = (flask.request.args.get('path') or '').strip()

    if not raw:
        root = flask.current_app.config.get('root_directory')
        base, prefix = (Path(root).parent if root else Path.home()), ''
    else:
        typed = Path(raw).expanduser()
        if raw.endswith(os.sep) and typed.is_dir():
            base, prefix = typed, ''
        else:
            base, prefix = typed.parent, typed.name

    try:
        entries = sorted(
            e for e in base.iterdir()
            if e.is_dir() and not e.name.startswith('.') and e.name.lower().startswith(prefix.lower())
        )[:20]
    except OSError:
        entries = []

    return flask.render_template('partials/path_suggestions.html', entries=entries)

