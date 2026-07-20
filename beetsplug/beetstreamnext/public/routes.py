from typing import Tuple, Any
from flask import render_template

from . import public_bp

from beetsplug.beetstreamnext.utils.general import get_server_info


@public_bp.route('/')
def home() -> str:
    stats = get_server_info(extended=False)
    stats['status'] = 'running'
    return render_template('index.html', stats=stats)


@public_bp.app_errorhandler(404)
def page_not_found(_e: Any) -> Tuple[str, int]:
    error = {
        'code': 404,
        'title': '*record scratches*',
        'message': "Looks like you're lost.",
    }
    return render_template('error.html', error=error), error['code']

