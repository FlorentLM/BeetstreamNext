from __future__ import annotations

import flask

from .. import admin_bp, admin_required, back_to

from beetsplug.beetstreamnext.core.logging import bsn_logger, mem_log
from beetsplug.beetstreamnext.core.maintenance import clear_requests_caches, sweep_stale_references, clear_offline_files
from beetsplug.beetstreamnext.core.health import start_scan, is_scanning, health_stats, flagged_songs
from beetsplug.beetstreamnext.core.external import start_audiomuse_analysis
from beetsplug.beetstreamnext.core.beets_interaction import read_config, write_config
from beetsplug.beetstreamnext.core.startup import restart_server
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.constants import SERVER_NAME


@admin_bp.route('/beets/config', methods=['POST'])
@admin_required
def route_save_beets_config() -> str:
    content = flask.request.form.get('content', '')
    ok, message = write_config(content)
    return flask.render_template(
        'partials/beets_config.html', beets_config=read_config(), ok=ok, message=message
    )


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

    if flask.request.headers.get('HX-Request'):
        return route_health_scan_status()

    flask.flash(message, 'success' if started else 'info')
    return back_to('maintenance')


@admin_bp.route('/maintenance/health-scan-status', methods=['GET'])
@admin_required
def route_health_scan_status() -> str:
    return flask.render_template('partials/health_scan_status.html', scanning=is_scanning(), **health_stats())


@admin_bp.route('/maintenance/flagged-songs', methods=['GET'])
@admin_required
def route_flagged_songs() -> str:
    return flask.render_template('partials/flagged_songs.html', flagged_songs=flagged_songs())


@admin_bp.route('/maintenance/logs', methods=['GET'])
@admin_required
def route_logs() -> flask.Response:
    return flask.jsonify({'lines': mem_log.recents})


@admin_bp.route('/maintenance/restart', methods=['POST'])
@admin_required
def route_restart_server() -> flask.Response:
    bsn_logger.warning(f'Server restart triggered by admin ({flask.session.get("username")}).')
    flask.flash('Restarting server... this page will reconnect automatically in a few seconds.', 'info')
    resp = back_to('maintenance')
    restart_server()
    return resp
