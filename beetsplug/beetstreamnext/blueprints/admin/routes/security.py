from __future__ import annotations
import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.accounts.security import rate_limiter
from beetsplug.beetstreamnext.core.config.store import settings_store


IP_LIST_SETTINGS = {'whitelist': 'ip_whitelist', 'blacklist': 'ip_blacklist'}

IP_LIST_META = {
    'whitelist': {
        'title': 'IP Whitelist',
        'hint': 'If non-empty, only listed IPs can reach the server. Loopback IPs are always allowed.',
        'placeholder': 'e.g. 192.168.1.42',
    },
    'blacklist': {
        'title': 'IP Blacklist',
        'hint': 'Listed IPs are denied access regardless of whitelist.',
        'placeholder': 'e.g. 203.0.113.5',
    },
}


def _ip_list_partial(list_type: str, message: str | None = None, ok: bool = True) -> str:

    key = IP_LIST_SETTINGS[list_type]

    return flask.render_template(
        'partials/ip_list.html',
        list_type=list_type,
        ip_list=IP_LIST_META[list_type],
        ips=settings_store.get(key),
        pinned=settings_store.pinned(key),
        message=message,
        ok=ok,
    )


@admin_bp.route('/settings/security/ip/<list_type>', methods=['GET'])
@admin_required
def route_ip_list(list_type: str) -> str:

    if list_type not in IP_LIST_SETTINGS:
        flask.abort(404)

    return _ip_list_partial(list_type)


@admin_bp.route('/settings/security/ip/<list_type>/add', methods=['POST'])
@admin_required
def route_ip_add(list_type: str) -> str:
    key = IP_LIST_SETTINGS.get(list_type)
    if key is None:
        flask.abort(404)

    ip = (flask.request.form.get('ip') or '').strip()
    if not ip:
        return _ip_list_partial(list_type, 'IP address is required.', ok=False)

    current = list(settings_store.get(key))
    if ip in current:
        return _ip_list_partial(list_type, f'{ip} is already in the {list_type}.', ok=False)

    try:
        settings_store.set(key, current + [ip])
    except (ValueError, PermissionError) as e:
        return _ip_list_partial(list_type, str(e), ok=False)

    return _ip_list_partial(list_type, f'Added {ip} to {list_type}.')


@admin_bp.route('/settings/security/ip/<list_type>/remove', methods=['POST'])
@admin_required
def route_ip_remove(list_type: str) -> str:

    key = IP_LIST_SETTINGS.get(list_type)

    if key is None:
        flask.abort(404)

    ip = (flask.request.form.get('ip') or '').strip()

    if ip in settings_store.pinned(key):
        return _ip_list_partial(
            list_type,
            f"'{ip}' is explicitly set via a CLI flag, environment variable, or config file. It can't be removed from here.",
            ok=False)

    current = list(settings_store.get(key))

    if ip not in current:
        return _ip_list_partial(list_type, f'{ip} not found in {list_type}.', ok=False)

    current.remove(ip)
    try:
        settings_store.set(key, current)
    except PermissionError as e:
        return _ip_list_partial(list_type, str(e), ok=False)

    return _ip_list_partial(list_type, f'Removed {ip} from {list_type}.')


@admin_bp.route('/maintenance/rate-limits', methods=['GET'])
@admin_required
def route_rate_limits() -> str:
    return flask.render_template('partials/rate_limit.html', report=rate_limiter.report())


@admin_bp.route('/maintenance/clear-rate-limits', methods=['POST'])
@admin_required
def route_clear_rate_limits() -> str:
    n = rate_limiter.purge()
    return flask.render_template(
        'partials/rate_limit.html',
        report=rate_limiter.report(),
        message=f'Cleared rate-limit state for {n} entr{"y" if n == 1 else "ies"}.',
        ok=True,
    )
