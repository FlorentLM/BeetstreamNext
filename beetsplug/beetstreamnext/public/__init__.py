from flask import Blueprint

public_bp = Blueprint('public', __name__)


from .routes import (
    errors,
    home,
    now_playing,
    shares,
)