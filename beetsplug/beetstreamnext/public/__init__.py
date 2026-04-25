from flask import Blueprint, render_template
from beetsplug.beetstreamnext.utils import get_server_info

public_bp = Blueprint('public', __name__)


@public_bp.route('/')
def home():
    stats = get_server_info(extended=False)
    return render_template('index.html', stats=stats)