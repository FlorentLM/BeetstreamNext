from flask import Blueprint

public_bp = Blueprint('public', __name__)


from .routes import (
    errors,
    health,
    home,
    tokens,
    now_playing,
    shares,
)