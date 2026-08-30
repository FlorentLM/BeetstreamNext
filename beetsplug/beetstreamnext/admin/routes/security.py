import flask

from .. import admin_bp, admin_required, back_to

from beetsplug.beetstreamnext.core.security import rate_limiter
from beetsplug.beetstreamnext.settings import settings_store


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
        return back_to('security')

    current = list(settings_store.get(key))
    if ip in current:
        flask.flash(f'{ip} is already in the {list_type}.', 'info')
    else:
        try:
            settings_store.set(key, current + [ip])
            flask.flash(f'Added {ip} to {list_type}.', 'success')
        except ValueError as e:
            flask.flash(str(e), 'error')

    return back_to('security')


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

    return back_to('security')


@admin_bp.route('/maintenance/rate-limits', methods=['GET'])
@admin_required
def route_rate_limits() -> flask.Response:
    return flask.jsonify(rate_limiter.report())


@admin_bp.route('/maintenance/clear-rate-limits', methods=['POST'])
@admin_required
def route_clear_rate_limits() -> flask.Response:
    n = rate_limiter.purge()
    flask.flash(f'Cleared rate-limit state for {n} entr{"y" if n == 1 else "ies"}.', 'success')
    return back_to('maintenance')
