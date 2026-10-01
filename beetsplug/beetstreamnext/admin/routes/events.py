from __future__ import annotations

import queue
import flask

from .. import admin_bp, admin_required
from beetsplug.beetstreamnext.core.events import admin_events
from beetsplug.beetstreamnext.constants import EVENT_BUS_INTERVAL


@admin_bp.route('/events')
@admin_required
def route_admin_events() -> flask.Response:
    """
    Shared SSE stream for the admin panel's live updating views.
    """
    def stream():
        with admin_events.subscribe() as q:
            yield 'retry: 2000\n\n'

            while True:
                try:
                    event, data = q.get(timeout=EVENT_BUS_INTERVAL)
                except queue.Empty:
                    yield ': heartbeat\n\n'
                    continue

                payload = '\n'.join(f'data: {line}' for line in data.splitlines() or [''])
                yield f'event: {event}\n{payload}\n\n'

    return flask.Response(stream(), mimetype='text/event-stream')
