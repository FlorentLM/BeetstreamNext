"""
BeetstreamNext is a Beets.io plugin that exposes OpenSubsonic API endpoints.
"""

from .application import app

# Register middleware with `before_request` and `after_request`
from . import middleware  # noqa: F401

# Register the blueprints
from .api import api_bp
from .public import public_bp
app.register_blueprint(api_bp)
app.register_blueprint(public_bp)

# And import the beets hook
from .beets_hook import BeetstreamNextPlugin