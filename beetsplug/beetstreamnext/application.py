from flask import Flask
from flask_wtf.csrf import CSRFProtect

from beetsplug.beetstreamnext.constants import PROJECT_ROOT, CACHE_LOCATION
from beetsplug.beetstreamnext.core.logging import LOG_LEVEL
from beetsplug.beetstreamnext.core.database import close_database

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
)
app.config['THUMBNAIL_CACHE_PATH'].mkdir(parents=True, exist_ok=True)

csrf = CSRFProtect(app)
