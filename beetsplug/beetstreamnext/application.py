import logging

from flask import Flask
from flask_wtf.csrf import CSRFProtect

from .constants import PROJECT_ROOT, CACHE_LOCATION, LOG_LEVEL, bsn_logger
from .db import close_database

##

app = Flask(
    __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/static',
)
app.teardown_appcontext(close_database)

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=3600,   # 1 hour
    WTF_CSRF_CHECK_DEFAULT=False,
    PROJECT_ROOT=PROJECT_ROOT,
    IMAGES_PATH=PROJECT_ROOT / 'static' / 'images',
    HTTP_CACHE_PATH=CACHE_LOCATION / 'httpcache.sqlite',
    THUMBNAIL_CACHE_PATH=CACHE_LOCATION / 'thumbnails',
)
app.config['THUMBNAIL_CACHE_PATH'].mkdir(parents=True, exist_ok=True)
# TODO: Add 'TRUSTED_HOSTS'

app.logger.setLevel(LOG_LEVEL)
logging.getLogger('flask').setLevel(LOG_LEVEL)
logging.getLogger('werkzeug').setLevel(LOG_LEVEL)
bsn_logger.propagate = True

csrf = CSRFProtect(app)
