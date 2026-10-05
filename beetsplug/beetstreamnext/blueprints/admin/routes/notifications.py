from __future__ import annotations

import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core import notifications
from beetsplug.beetstreamnext.core.notifications import EVENTS, IMPORT_COMPLETED, VARIABLES


def _get_fields() -> dict:
    form = flask.request.form
    return {
        'event': form.get('event', ''),
        'title': form.get('title', ''),
        'body': form.get('body', ''),
        'urls': form.getlist('url'),
    }


@admin_bp.route('/notifications', methods=['GET'])
@admin_required
def route_notifications() -> str:
    return flask.render_template(
        'partials/notification_list.html',
        apprise=notifications.APPRISE,
        items=notifications.list_notifications() if notifications.APPRISE else [],
        events=EVENTS, variables=VARIABLES
    )


@admin_bp.route('/notifications/new', methods=['GET'])
@admin_required
def route_notifications_new() -> str:
    """Blank form. Changing the event type re-fetches."""

    event = EVENTS.get(flask.request.args.get('event', ''), IMPORT_COMPLETED)

    item = {
        'id': None,
        'event': event.key,
        'title': event.label,
        'body': event.body,
        'urls': flask.request.args.getlist('url'),
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


@admin_bp.route('/notifications/<int:notification_id>/enabled', methods=['POST'])
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
