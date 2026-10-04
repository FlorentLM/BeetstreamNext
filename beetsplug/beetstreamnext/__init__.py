"""
BeetstreamNext is a Beets.io plugin that exposes OpenSubsonic API endpoints.
"""
from __future__ import annotations

from .application import app, csrf

# Register middleware with `before_request` and `after_request`
from . import middleware  # noqa: F401

# Register the blueprints
from .subsonic import subsonic_bp
from .public import public_bp
from .admin import admin_bp
from .account import account_bp
from .auth import auth_bp
app.register_blueprint(subsonic_bp)
csrf.exempt(subsonic_bp)
app.register_blueprint(public_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(account_bp)
app.register_blueprint(auth_bp)

# And import the beets hook
from .entrypoints.beetsplugin_hook import BeetstreamNextPlugin