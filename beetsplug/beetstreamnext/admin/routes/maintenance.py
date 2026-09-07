from typing import List
from itertools import groupby
import flask

from .. import admin_bp, admin_required, back_to

from beetsplug.beetstreamnext.core.logging import bsn_logger, mem_log
from beetsplug.beetstreamnext.core.maintenance import clear_requests_caches, sweep_stale_references, clear_offline_files
from beetsplug.beetstreamnext.core.health import start_scan, is_scanning, health_stats
from beetsplug.beetstreamnext.core.external import start_audiomuse_analysis
from beetsplug.beetstreamnext.core.beets_interaction import start_import, is_importing, read_config, write_config
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.constants import SERVER_NAME, BEETS_IMPORT_LOG_PATH


def drop_python_tracebacks(log_lines: List[str]) -> List[str]:
    python_tracebacks = (
        'The above exception was the direct cause of the following exception:',
        'During handling of the above exception, another exception occurred:',
        'Traceback (most recent call last):'
    )
    kept = []
    for _, group in groupby(log_lines, key=bool):
        chunk = list(group)
        if not chunk[0].startswith(python_tracebacks):
            kept.extend(chunk)

    result = []
    for line, group in groupby(kept, key=bool):
        if line:
            result.extend(group)
        else:
            result.append('')
    return result


@admin_bp.route('/beets/scan', methods=['POST'])
@admin_required
def route_beets_scan() -> flask.Response:
    ok, message, already_running = start_import()
    flask.flash(message, 'info' if already_running else ('success' if ok else 'error'))
    return back_to('beets')


@admin_bp.route('/beets/scan-status', methods=['GET'])
@admin_required
def route_beets_scan_status() -> flask.Response:
    lib = flask.current_app.config['lib']
    try:
        with lib.transaction() as tx:
            items_count = tx.query("SELECT COUNT(*) FROM items")[0][0]
    except Exception as e:
        bsn_logger.warning(f'Could not read item count while checking scan status: {e}')
        items_count = None

    return flask.jsonify({'scanning': is_importing(), 'count': items_count})


@admin_bp.route('/beets/import-log', methods=['GET'])
@admin_required
def route_beets_import_log() -> flask.Response:
    try:
        with open(BEETS_IMPORT_LOG_PATH, 'r', errors='replace') as f:
            lines = drop_python_tracebacks(f.read().splitlines()[-1000:])
    except OSError:
        lines = []

    return flask.jsonify({'lines': lines})


@admin_bp.route('/beets/config', methods=['GET'])
@admin_required
def route_read_beets_config() -> flask.Response:
    return flask.jsonify(read_config())


@admin_bp.route('/beets/config', methods=['POST'])
@admin_required
def route_save_beets_config() -> flask.Response:
    content = (flask.request.get_json(silent=True) or {}).get('content', '')
    ok, message = write_config(content)
    return flask.jsonify({'ok': ok, 'message': message, **read_config()})


@admin_bp.route('/maintenance/clear-cache', methods=['POST'])
@admin_required
def route_clear_cache() -> flask.Response:
    try:
        cleared = clear_requests_caches(
            flask.current_app.config['THUMBNAIL_CACHE_PATH'],
            flask.current_app.config['HTTP_CACHE_PATH']
        )
        if cleared:
            flask.flash(f"Cleared: {', '.join(cleared)}.", 'success')
        else:
            flask.flash('Nothing to clear.', 'info')
    except RuntimeError as e:
        flask.flash(str(e), 'error')

    return back_to('maintenance')


@admin_bp.route('/maintenance/database-cleanup', methods=['POST'])
@admin_required
def route_database_cleanup() -> flask.Response:
    try:
        purged = sweep_stale_references()
        if purged:
            details = ', '.join(f'{n} {label}' for label, n in purged.items())
            flask.flash(f'Purged stale references: {details}.', 'success')
        else:
            flask.flash('No stale references found.', 'info')

    except Exception as e:
        err = f'{SERVER_NAME} database cleanup failed: {e}'
        bsn_logger.error(err)
        flask.flash(err, 'error')

    return back_to('maintenance')


@admin_bp.route('/maintenance/cleanup-offline-files', methods=['POST'])
@admin_required
def route_cleanup_offlines() -> flask.Response:
    try:
        purged = clear_offline_files()
        if purged:
            details = ', '.join(f'{n} {label}' for label, n in purged.items())
            flask.flash(f'Swept: {details}.', 'success')
        else:
            flask.flash('Nothing to sweep.', 'info')
    except Exception as e:
        err = f'{SERVER_NAME} cache sweep failed: {e}'
        bsn_logger.error(err)
        flask.flash(err, 'error')

    return back_to('maintenance')


@admin_bp.route('/maintenance/audiomuse-fingerprint', methods=['POST'])
@admin_required
def route_audiomuse_fingerprint() -> flask.Response:
    ok, message = start_audiomuse_analysis()
    flask.flash(message, 'success' if ok else 'error')
    return back_to('maintenance')


@admin_bp.route('/maintenance/health-scan', methods=['POST'])
@admin_required
def route_health_scan() -> flask.Response:
    full = flask.request.form.get('full', type=safe_str) == '1'
    started, message = start_scan(full=full)
    flask.flash(message, 'success' if started else 'info')
    return back_to('maintenance')


@admin_bp.route('/maintenance/health-scan-status', methods=['GET'])
@admin_required
def route_health_scan_status() -> flask.Response:
    return flask.jsonify({'scanning': is_scanning(), **health_stats()})


@admin_bp.route('/maintenance/logs', methods=['GET'])
@admin_required
def route_logs() -> flask.Response:
    return flask.jsonify({'lines': mem_log.recents})
