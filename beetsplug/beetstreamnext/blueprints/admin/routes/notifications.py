from __future__ import annotations

import time
import flask
from markupsafe import Markup, escape

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core import notifications
from beetsplug.beetstreamnext.core.notifications import EVENTS, RE_VARIABLE, VARIABLES


@admin_bp.app_template_filter('sample_preview')
def sample_preview(text: str, event: str) -> Markup:
    """Escape a template and fill its {variables} with the event's sample values."""

    samples = {**EVENTS[event].examples(), 'time': time.strftime('%Y-%m-%d %H:%M:%S')}

    def fill(m) -> str:
        if m.group(1) not in samples:
            return m.group(0)
        return f'<mark class="variable-sample">{escape(samples[m.group(1)])}</mark>'

    return Markup(RE_VARIABLE.sub(fill, str(escape(text))))


def _get_fields() -> dict:
    form = flask.request.form
    event = EVENTS.get(form.get('event', ''))

    # checkboxes first post a hidden 0 so last value wins
    options = {
        o.key: form.getlist(f'opt_{o.key}')[-1] for o in event.options if form.getlist(f'opt_{o.key}')
    } if event else {}

    return {
        'event': form.get('event', ''),
        'title': form.get('title', ''),
        'body': form.get('body', ''),
        'urls': form.getlist('url'),
        'options': options,
    }


@admin_bp.route('/notifications', methods=['GET'])
@admin_required
def route_notifications() -> str:

    items = notifications.list_notifications() if notifications.APPRISE else []
    url_suggestions = list(dict.fromkeys(u for item in items for u in item['urls']))

    return flask.render_template(
        'partials/notification_list.html',
        apprise=notifications.APPRISE,
        items=items,
        url_suggestions=url_suggestions,
        services=notifications.list_services(),
        events=EVENTS, variables=VARIABLES,
        service_names=notifications.service_names
    )


@admin_bp.route('/notifications/new', methods=['GET'])
@admin_required
def route_notifications_new() -> str:
    """Blank form. Changing the event type re-fetches."""

    key = flask.request.args.get('event', '')
    if key not in EVENTS:
        key = 'import_completed'
    event = EVENTS[key]

    item = {
        'id': None,
        'event': key,
        'title': event.label,
        'body': event.body,
        'urls': flask.request.args.getlist('url'),
        'options': event.default_options(),
        'enabled': True
    }

    return flask.render_template('partials/notification_form.html', item=item, events=EVENTS, variables=VARIABLES)


@admin_bp.route('/notifications/add', methods=['POST'])
@admin_required
def route_notifications_add() -> flask.Response:

    fields = _get_fields()
    enabled = flask.request.form.get('enabled') == '1'

    try:
        notifications.add_notification(**fields, enabled=enabled)

    except ValueError as e:
        item = {
            'id': None,
            'enabled': enabled,
            **fields
        }
        return flask.make_response(
            flask.render_template('partials/notification_form.html',
                                  item=item, message=str(e), ok=False, events=EVENTS, variables=VARIABLES))

    resp = flask.make_response('')                      # Closes the new form
    resp.headers['HX-Trigger'] = 'notification-added'   # List reloads

    return resp


@admin_bp.route('/notifications/<int:notification_id>/update', methods=['POST'])
@admin_required
def route_notifications_update(notification_id: int) -> flask.Response:

    fields = _get_fields()
    current = notifications.get_notification(notification_id)

    if not current:
        flask.abort(404)

    try:
        notifications.update_notification(notification_id, **fields)

    except ValueError as e:
        item = {
            'id': notification_id,
            'enabled': current['enabled'],
            **fields
        }
        return flask.make_response(
            flask.render_template('partials/notification_form.html',
                                  item=item, message=str(e), ok=False,
                                  open=True, events=EVENTS, variables=VARIABLES))

    return flask.make_response(
        flask.render_template('partials/notification_form.html',
                              item=notifications.get_notification(notification_id),
                              open=True, events=EVENTS, variables=VARIABLES))


@admin_bp.route('/notifications/<int:notification_id>/card', methods=['GET'])
@admin_required
def route_notifications_card(notification_id: int) -> str:
    """Saved version of a card, used by Cancel to discard unsaved edits."""

    item = notifications.get_notification(notification_id)
    if not item:
        flask.abort(404)

    return flask.render_template('partials/notification_form.html',
                                 item=item, open=True, events=EVENTS, variables=VARIABLES)


@admin_bp.route('/notifications/url-row', methods=['GET'])
@admin_required
def route_notifications_url_row() -> str:
    """Add an empty URL input row."""
    return flask.render_template('partials/notification_url_row.html', url='')


@admin_bp.route('/notifications/<int(signed=True):notification_id>/enabled', methods=['POST'])
@admin_required
def route_notifications_enabled(notification_id: int) -> flask.Response:

    enabled = '1' in flask.request.form.getlist('enabled')

    if not notifications.toggle_enabled(notification_id, enabled):
        flask.abort(404)

    return flask.make_response('', 204)


@admin_bp.route('/notifications/<int:notification_id>/delete', methods=['POST'])
@admin_required
def route_notifications_delete(notification_id: int) -> flask.Response:
    notifications.delete_notification(notification_id)
    return flask.make_response('')      # Card removes itself


@admin_bp.route('/notifications/test', methods=['POST'])
@admin_required
def route_notifications_test() -> str:
    ok, message = notifications.send_test(**_get_fields())
    return flask.render_template('partials/test_result.html', result_id='', ok=ok, message=message)


@admin_bp.route('/notifications/<int(signed=True):notification_id>/test', methods=['POST'])
@admin_required
def route_notifications_test_saved(notification_id: int) -> str:
    """Test send a config-defined notification"""

    item = next((n for n in notifications.CONFIG_NOTIFICATIONS if n['id'] == notification_id), None)
    if not item:
        flask.abort(404)

    ok, message = notifications.send_test(item['event'], item['title'], item['body'], item['urls'])
    return flask.render_template('partials/test_result.html', result_id='', ok=ok, message=message)


@admin_bp.route('/notifications/<int:notification_id>/retype', methods=['POST'])
@admin_required
def route_notifications_retype(notification_id: int) -> str:
    """Saved card after type change: new variables and options but text kept (unsaved)."""

    current = notifications.get_notification(notification_id)
    if not current:
        flask.abort(404)

    fields = _get_fields()
    event = EVENTS.get(fields['event'])
    if event is None:
        flask.abort(400)

    item = {'id': notification_id, 'enabled': current['enabled'], **fields, 'options': event.default_options()}

    return flask.render_template('partials/notification_form.html',
                                 item=item, dirty=True, open=True, events=EVENTS, variables=VARIABLES)
