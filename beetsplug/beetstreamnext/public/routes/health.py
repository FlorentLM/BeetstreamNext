import time
import flask

from .. import public_bp

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import SERVER_VERSION, START_TIME
from beetsplug.beetstreamnext.core.database import database


@public_bp.route('/healthz')
def healthz() -> flask.Response:
    """
    Unauthenticated readiness check for orchestrators (Docker, k8s, uptime monitors, etc).
    """

    checks = {}

    try:
        with app.config['lib'].transaction() as tx:
            tx.query("SELECT 1")
        checks['beets_library'] = True
    except Exception:
        checks['beets_library'] = False

    try:
        database().execute("SELECT 1")
        checks['database'] = True
    except Exception:
        checks['database'] = False

    healthy = all(checks.values())

    body = {
        'status': 'ok' if healthy else 'error',
        'version': SERVER_VERSION,
        'uptime_seconds': int(time.time() - START_TIME),
        'checks': checks,
    }

    return flask.jsonify(body), 200 if healthy else 503
