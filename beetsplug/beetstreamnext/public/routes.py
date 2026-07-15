from flask import render_template

from . import public_bp

from beetsplug.beetstreamnext.utils.general import get_server_info


@public_bp.route('/')
def home():
    stats = get_server_info(extended=False)
    stats['status'] = 'running'
    return render_template('index.html', stats=stats)


@public_bp.app_errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404
