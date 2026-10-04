from __future__ import annotations

import queue
import time
import flask

from .. import admin_bp, admin_required
from beetsplug.beetstreamnext.core.services.events import admin_events
from beetsplug.beetstreamnext.core.accounts.users_crud import get_user_roles
from beetsplug.beetstreamnext.constants import EVENT_BUS_INTERVAL


@admin_bp.route('/events')
@admin_required
def route_admin_events() -> flask.Response:
    """
    Shared SSE stream for the admin panel's live updating views.
    """
    username = flask.session.get('username')
    app = flask.current_app._get_current_object()   # the stream runs after the request context is gone

    def stream():
        with admin_events.subscribe() as q:
            yield 'retry: 2000\n\n'
            last_check = time.monotonic()

            while True:
                # Auth is checked at connect by @admin_required but this re-checks periodically
                # to prevent demoted admin from continuing to receive events on an open stream
                if time.monotonic() - last_check >= EVENT_BUS_INTERVAL:
                    with app.app_context():
                        roles = get_user_roles(username)

                    if not (roles and roles.get('adminRole')):
                        return

                    last_check = time.monotonic()

                try:
                    event, data = q.get(timeout=EVENT_BUS_INTERVAL)
                except queue.Empty:
                    yield ': heartbeat\n\n'
                    continue

                payload = '\n'.join(f'data: {line}' for line in data.splitlines() or [''])
                yield f'event: {event}\n{payload}\n\n'

    return flask.Response(stream(), mimetype='text/event-stream')
