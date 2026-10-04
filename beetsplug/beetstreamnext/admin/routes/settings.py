from __future__ import annotations

from typing import Any
import flask

from .. import admin_bp, admin_required, back_to

from beetsplug.beetstreamnext.admin.routes.chat import chat_page_context
from beetsplug.beetstreamnext.admin.routes.security import IP_LIST_META
from beetsplug.beetstreamnext.utils.general import get_server_info
from beetsplug.beetstreamnext.utils.text import format_bytes
from beetsplug.beetstreamnext.core.logging import bsn_logger, mem_log
from beetsplug.beetstreamnext.core.maintenance import cache_breakdown
from beetsplug.beetstreamnext.core.beets_interaction import read_config, is_import_safe
from beetsplug.beetstreamnext.core.users_crud import list_users
from beetsplug.beetstreamnext.core.radio import list_radios
from beetsplug.beetstreamnext.core.external import test_lastfm_connection, test_audiomuse_connection, test_podcastindex_connection
from beetsplug.beetstreamnext.utils.system import is_writable
from beetsplug.beetstreamnext.constants import RADIO_BROWSER
from beetsplug.beetstreamnext.schemas import SETTINGS_SCHEMA, SETTINGS_CATEGORIES, PUBLIC_USER_FIELDS
from beetsplug.beetstreamnext.forms import UserForm, RadioStationForm
from beetsplug.beetstreamnext.settings import settings_store


_CAN_MULTISELECT = {'external_playlists_editors'}


##
# Settings-updating routes

@admin_bp.route('/settings/<category>', methods=['POST'])
@admin_required
def route_update_settings(category: str) -> flask.Response:

    if category not in SETTINGS_CATEGORIES:
        flask.abort(404)

    submitted = flask.request.form
    errors: list[str] = []
    updated: list[str] = []
    restart_needed = False

    for key, spec in SETTINGS_SCHEMA.items():
        if spec.get('category') != category:
            continue
        if spec['type'] == 'list[str]' and key not in _CAN_MULTISELECT:
            continue   # Handled by dedicated endpoints
        if settings_store.locked(key):
            continue   # set explicitly (via CLI/env/config) so not editable here
        if key == 'allow_disk_writes' and is_import_safe():
            continue   # beets config already never writes/copies/moves files, so this is a no-op

        if spec['type'] == 'bool':
            value: Any = key in submitted
        elif key in _CAN_MULTISELECT:
            value = submitted.getlist(key)
        elif key in submitted:
            value = submitted[key]
            if spec.get('sensitive') and value == '':
                continue   # leave unchanged
        else:
            continue

        if not settings_store.would_change(key, value):
            continue   # field unchanged

        try:
            settings_store.set(key, value)
            updated.append(key)
            if spec.get('requires_restart'):
                restart_needed = True
        except (ValueError, TypeError) as e:
            errors.append(f"{key}: {e}")
            bsn_logger.warning(f"Invalid value submitted for '{key}': {e}")
        except PermissionError as e:
            errors.append(str(e))
        except Exception as e:
            # .set() re-raises applicable failures after persisting
            errors.append(f'{key}: saved, but failed to apply: {e}')
            bsn_logger.error(f"Live-apply failed for '{key}': {e}")

    for err in errors:
        flask.flash(err, 'error')

    if updated and not errors:
        plur = 's' if len(updated) > 1 else ''
        msg = f'Updated {len(updated)} setting{plur}.'
        if restart_needed:
            msg += ' Some changes require a server restart to take effect.'
        flask.flash(msg, 'info' if restart_needed else 'success')
    elif not updated and not errors:
        flask.flash('No changes.', 'info')

    return back_to(category)


##
# Reset a setting to its default (deletes the stored db row)

@admin_bp.route('/settings/<category>/clear/<key>', methods=['POST'])
@admin_required
def route_clear_setting(category: str, key: str) -> flask.Response:
    if category not in SETTINGS_CATEGORIES:
        flask.abort(404)
    spec = SETTINGS_SCHEMA.get(key)
    if not spec or spec.get('category') != category:
        flask.abort(404)

    try:
        settings_store.reset(key)
        flask.flash(f"Reset '{key}' to default.", 'success')
    except PermissionError as e:
        flask.flash(str(e), 'error')

    return back_to(category)


##
# Library integrations: test connection

def test_result(result_id: str, ok: bool, message: str) -> str:
    return flask.render_template('partials/test_result.html', result_id=result_id, ok=ok, message=message)


@admin_bp.route('/settings/test/lastfm', methods=['GET'])
@admin_required
def route_test_lastfm() -> flask.Response:
    ok, message = test_lastfm_connection()
    return test_result('test-result-lastfm', ok, message)


@admin_bp.route('/settings/test/audiomuse', methods=['GET'])
@admin_required
def route_test_audiomuse() -> flask.Response:
    ok, message = test_audiomuse_connection()
    return test_result('test-result-audiomuse', ok, message)


@admin_bp.route('/settings/test/podcastindex', methods=['GET'])
@admin_required
def route_test_podcastindex() -> flask.Response:
    ok, message = test_podcastindex_connection()
    return test_result('test-result-podcastindex', ok, message)


@admin_bp.route('/')
@admin_required
def route_settings() -> flask.Response:
    settings_by_category = {cat: settings_store.get_for_ui(cat) for cat in SETTINGS_CATEGORIES}
    host_suggestions = flask.current_app.config.get('HOST_LIST', [])

    # Small contextual pills next to a setting
    setting_pills: dict[str, tuple[str, str]] = {}

    music_root = flask.current_app.config.get('root_directory')
    music_ro = bool(music_root) and not is_writable(music_root)

    library_db_path = flask.current_app.config.get('BEETS_DB_PATH')
    library_ro = bool(library_db_path) and not is_writable(library_db_path)

    if music_ro:
        for key in ('save_album_art', 'follow_playlist_embedded_urls'):
            setting_pills[key] = ('music folder is read-only', 'danger')

    if library_ro:
        for key in ('save_lyrics', 'save_album_version', 'ratings_writeback_user'):
            setting_pills[key] = ('beets library is read-only', 'danger')

    if is_import_safe():
        setting_pills['allow_disk_writes'] = ('move/copy/write are all off', 'info')
        settings_by_category['library']['allow_disk_writes']['locked'] = True
        settings_by_category['library']['allow_disk_writes']['lock_reason'] = (
            "This setting has no effect, Beets already avoids modifying files."
        )
    elif music_ro and library_ro:
        setting_pills['allow_disk_writes'] = ('music folder & library are read-only', 'danger')
    elif music_ro:
        setting_pills['allow_disk_writes'] = ('music folder is read-only', 'danger')
    elif library_ro:
        setting_pills['allow_disk_writes'] = ('beets library is read-only', 'danger')

    cache_bytes = cache_breakdown(
        flask.current_app.config['THUMBNAIL_CACHE_PATH'],
        flask.current_app.config['HTTP_CACHE_PATH']
    )
    cache_sizes = {label: format_bytes(n) for label, n in cache_bytes.items() if n > 0}
    cache_size = format_bytes(sum(cache_bytes.values()))

    users = list_users(fields=list(PUBLIC_USER_FIELDS) + ['avatarLastChanged'])

    resp = flask.make_response(
        flask.render_template(
            'settings.html',
            users=users,
            **chat_page_context(flask.request.args.get('chat_page', default=1, type=int)),
            cache_size=cache_size,
            cache_sizes=cache_sizes,
            radios=list_radios(),
            IP_LIST_META=IP_LIST_META,
            radio_discovery_enabled=flask.current_app.config.get('enable_radio_discovery', False) and RADIO_BROWSER,
            podcast_discovery_enabled=flask.current_app.config['podcast_manager'].discovery_enabled,
            beets_schema_drift=flask.current_app.config.get('BEETS_SCHEMA_DRIFT', {}),
            beets_config=read_config(),
            create_form=UserForm(formdata=None),
            radio_form=RadioStationForm(formdata=None),
            server_info=get_server_info(extended=True),
            current_username=flask.session.get('username'),
            settings_categories=SETTINGS_CATEGORIES,
            settings_by_category=settings_by_category,
            setting_pills=setting_pills,
            host_suggestions=host_suggestions,
            log_lines=mem_log.recents,
        )
    )
    return resp