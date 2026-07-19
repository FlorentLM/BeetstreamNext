from typing import Any
import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.utils.general import get_server_info
from beetsplug.beetstreamnext.constants import bsn_logger
from beetsplug.beetstreamnext.core.security import rate_limiter
from beetsplug.beetstreamnext.core.maintenance import clear_caches
from beetsplug.beetstreamnext.core.users_crud import load_all_users
from beetsplug.beetstreamnext.schemas import SETTINGS_SCHEMA, SETTINGS_CATEGORIES, PUBLIC_USER_FIELDS, USER_ROLES_SCHEMA
from beetsplug.beetstreamnext.admin.forms import UserForm, EditUserForm
from beetsplug.beetstreamnext.settings import settings_store


def _back_to(anchor: str) -> flask.Response:
    return flask.redirect(flask.url_for('admin.route_settings') + f'#{anchor}')


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
        if spec['type'] == 'list[str]':
            continue   # Handled by dedicated endpoints

        if spec['type'] == 'bool':
            value: Any = key in submitted
        elif key in submitted:
            value = submitted[key]
            if spec.get('sensitive') and value == '':
                continue   # leave unchanged
        else:
            continue

        try:
            current = settings_store.get(key)
            new_value = settings_store.set(key, value)
            if new_value != current:
                updated.append(key)
                if spec.get('requires_restart'):
                    restart_needed = True
        except (ValueError, TypeError) as e:
            errors.append(f"{key}: {e}")
            bsn_logger.warning(f"Invalid value submitted for '{key}': {e}")
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

    return _back_to(category)


##
# Sensitive settings: dedicated clearing endpoint

@admin_bp.route('/settings/<category>/clear/<key>', methods=['POST'])
@admin_required
def route_clear_setting(category: str, key: str) -> flask.Response:
    if category not in SETTINGS_CATEGORIES:
        flask.abort(404)
    spec = SETTINGS_SCHEMA.get(key)
    if not spec or spec.get('category') != category or not spec.get('sensitive'):
        flask.abort(404)

    settings_store.set(key, '')
    flask.flash(f"Cleared '{key}'.", 'success')
    return _back_to(category)


##
# IP whitelist / blacklist

_IP_LIST_SETTINGS = {'whitelist': 'ip_whitelist', 'blacklist': 'ip_blacklist'}


@admin_bp.route('/settings/security/ip/<list_type>/add', methods=['POST'])
@admin_required
def route_ip_add(list_type: str) -> flask.Response:
    key = _IP_LIST_SETTINGS.get(list_type)
    if key is None:
        flask.abort(404)

    ip = (flask.request.form.get('ip') or '').strip()
    if not ip:
        flask.flash('IP address is required.', 'error')
        return _back_to('security')

    current = list(settings_store.get(key))
    if ip in current:
        flask.flash(f'{ip} is already in the {list_type}.', 'info')
    else:
        try:
            settings_store.set(key, current + [ip])
            flask.flash(f'Added {ip} to {list_type}.', 'success')
        except ValueError as e:
            flask.flash(str(e), 'error')

    return _back_to('security')


@admin_bp.route('/settings/security/ip/<list_type>/remove', methods=['POST'])
@admin_required
def route_ip_remove(list_type: str) -> flask.Response:
    key = _IP_LIST_SETTINGS.get(list_type)
    if key is None:
        flask.abort(404)

    ip = (flask.request.form.get('ip') or '').strip()
    current = list(settings_store.get(key))

    if ip in current:
        current.remove(ip)
        settings_store.set(key, current)
        flask.flash(f'Removed {ip} from {list_type}.', 'success')
    else:
        flask.flash(f'{ip} not found in {list_type}.', 'info')

    return _back_to('security')


##
# Maintenance

@admin_bp.route('/maintenance/clear-cache', methods=['POST'])
@admin_required
def route_clear_cache() -> flask.Response:
    try:
        cleared = clear_caches(
            flask.current_app.config['THUMBNAIL_CACHE_PATH'],
            flask.current_app.config['HTTP_CACHE_PATH']
        )
        if cleared:
            flask.flash(f"Cleared: {', '.join(cleared)}.", 'success')
        else:
            flask.flash('Nothing to clear.', 'info')
    except RuntimeError as e:
        flask.flash(str(e), 'error')

    return _back_to('maintenance')


@admin_bp.route('/maintenance/rate-limits', methods=['GET'])
@admin_required
def route_rate_limits() -> flask.Response:
    """Currently-blocked and warning-state IPs as JSON for the live panel."""
    return flask.jsonify(rate_limiter.report())


@admin_bp.route('/maintenance/clear-rate-limits', methods=['POST'])
@admin_required
def route_clear_rate_limits() -> flask.Response:
    n = rate_limiter.purge()
    flask.flash(f'Cleared rate-limit state for {n} IP(s).', 'success')
    return _back_to('maintenance')


@admin_bp.route('/')
@admin_required
def route_settings() -> flask.Response:

    # Grab the 1-time display API key
    new_api_key = flask.session.pop('_new_api_key', None)

    settings_by_category = {cat: settings_store.get_for_ui(cat) for cat in SETTINGS_CATEGORIES}

    users = load_all_users(fields=list(PUBLIC_USER_FIELDS) + ['avatarLastChanged'])
    for u in users:
        # just a bool for the UI
        u['hasAvatar'] = bool(u.get('avatarLastChanged'))

    resp = flask.make_response(
        flask.render_template(
            'settings.html',
            users=users,
            create_form=UserForm(formdata=None),   # Must not repopulate from a failed POST
            edit_form=EditUserForm(formdata=None),
            role_fields=[(name, label) for name, label, _ in USER_ROLES_SCHEMA],
            server_info=get_server_info(extended=True),
            current_username=flask.session.get('username'),
            new_api_key=new_api_key,
            settings_categories=SETTINGS_CATEGORIES,
            settings_by_category=settings_by_category,
        )
    )
    return resp
