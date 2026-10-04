from __future__ import annotations

import os
from pathlib import Path
import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.beets_interaction import htmlify_log, start_import, import_status, is_import_running, send_import_input, read_config, start_pinned_imports
from beetsplug.beetstreamnext.core.import_paths import list_pinned_paths, add_pinned_path, remove_pinned_path, set_pinned_incremental, set_pinned_watch


@admin_bp.route('/beets/import-log', methods=['GET'])
@admin_required
def route_beets_import_log() -> flask.Response:
    return flask.jsonify({'lines': htmlify_log()})


@admin_bp.route('/beets/config', methods=['GET'])
@admin_required
def route_read_beets_config() -> str:
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
def route_beets_import_browse() -> str:

    raw = (flask.request.args.get('path') or '').strip()

    if not raw:
        return flask.render_template('partials/path_suggestions.html', entries=[])

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



def _pinned_paths_partial(message: str = '', ok: bool = True) -> str:
    return flask.render_template(
        'partials/pinned_paths.html', pinned=list_pinned_paths(), message=message, ok=ok, running=is_import_running()
    )


@admin_bp.route('/beets/pinned', methods=['GET'])
@admin_required
def route_pinned_paths() -> str:
    return _pinned_paths_partial()


@admin_bp.route('/beets/pinned/add', methods=['POST'])
@admin_required
def route_pinned_paths_add() -> str:
    path = (flask.request.form.get('path') or '').strip()
    ok, message = add_pinned_path(path, incremental=flask.request.form.get('incremental') == '1')
    return _pinned_paths_partial(message, ok)


@admin_bp.route('/beets/pinned/<int:path_id>/remove', methods=['POST'])
@admin_required
def route_pinned_paths_remove(path_id: int) -> str:
    remove_pinned_path(path_id)
    return _pinned_paths_partial()


@admin_bp.route('/beets/pinned/<int:path_id>/incremental', methods=['POST'])
@admin_required
def route_pinned_paths_incremental(path_id: int) -> str:
    set_pinned_incremental(path_id, flask.request.form.get('incremental') == '1')
    return _pinned_paths_partial()


@admin_bp.route('/beets/pinned/<int:path_id>/watch', methods=['POST'])
@admin_required
def route_pinned_paths_watch(path_id: int) -> str:
    set_pinned_watch(path_id, flask.request.form.get('watch') == '1')
    return _pinned_paths_partial()


@admin_bp.route('/beets/pinned/scan', methods=['POST'])
@admin_required
def route_pinned_paths_scan() -> str:
    ok, message = start_pinned_imports(list_pinned_paths())
    return _pinned_paths_partial(message, ok)
