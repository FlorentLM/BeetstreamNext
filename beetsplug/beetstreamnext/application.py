import functools
import flask
from flask import Flask
from flask_wtf.csrf import CSRFProtect

from beetsplug.beetstreamnext.constants import PROJECT_ROOT, CACHE_LOCATION
from beetsplug.beetstreamnext.core.logging import LOG_LEVEL
from beetsplug.beetstreamnext.core.database import close_database
from beetsplug.beetstreamnext.utils.text import format_duration

##

app = Flask(
    __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/static',
)

app.logger.setLevel(LOG_LEVEL)

app.teardown_appcontext(close_database)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=3600,   # 1 hour
    WTF_CSRF_CHECK_DEFAULT=True,
    PROJECT_ROOT=PROJECT_ROOT,
    IMAGES_PATH=PROJECT_ROOT / 'static' / 'images',
    HTTP_CACHE_PATH=CACHE_LOCATION / 'httpcache.sqlite',
    THUMBNAIL_CACHE_PATH=CACHE_LOCATION / 'thumbnails',
    TRUSTED_HOSTS='',
    STANDALONE_MODE=False,
)
app.config['THUMBNAIL_CACHE_PATH'].mkdir(parents=True, exist_ok=True)

app.jinja_env.filters['duration'] = format_duration

csrf = CSRFProtect(app)


##
# Small decorator for background threads to get the app context


def with_app_context(fn):
    """
    Lets a function run from a plain background thread (which has no Flask app/request context),
    by pushing one (and populating flask.g.lib) if it's missing.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if flask.has_app_context():
            if 'lib' not in flask.g:
                flask.g.lib = app.config['lib']
            return fn(*args, **kwargs)
        with app.app_context():
            flask.g.lib = app.config['lib']
            return fn(*args, **kwargs)
    return wrapper
