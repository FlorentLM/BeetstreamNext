from flask import Blueprint

public_bp = Blueprint('public', __name__)


from .routes import (
    errors,
    home,
    tokens,
    now_playing,
    shares,
)