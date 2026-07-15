from flask import Blueprint

public_bp = Blueprint('public', __name__)


from .routes import (
    home,
    page_not_found
)