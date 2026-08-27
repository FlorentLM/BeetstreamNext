from typing import Any, Tuple

from flask import render_template

from .. import public_bp


@public_bp.app_errorhandler(404)
def page_not_found(_e: Any) -> Tuple[str, int]:
    error = {
        'code': 404,
        'title': '*record scratches*',
        'message': "Looks like you're lost.",
    }
    return render_template('error.html', error=error), error['code']
