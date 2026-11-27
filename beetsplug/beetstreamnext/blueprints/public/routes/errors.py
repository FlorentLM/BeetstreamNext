from __future__ import annotations
from typing import Any

import flask

from .. import public_bp


@public_bp.app_errorhandler(404)
def page_not_found(_e: Any) -> tuple[str, int]:
    if flask.request.path.startswith(('/rest', '/tokenised-')):
        return '', 404

    error = {
        'code': 404,
        'title': '*record scratches*',
        'message': "Looks like you're lost.",
    }
    return flask.render_template('error.html', error=error), 404
