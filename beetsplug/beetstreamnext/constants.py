import os
import platform
import re
import shutil
import importlib
import logging
from pathlib import Path


SUBSONIC_API_VER = '1.16.1'
BEETSTREAMNEXT_VER = '1.6.0-dev'


# LOG_LEVEL = logging.ERROR
LOG_LEVEL = logging.INFO
# LOG_LEVEL = logging.DEBUG

logging.basicConfig(encoding='utf-8', level=LOG_LEVEL)
bsn_logger = logging.getLogger('beetstreamnext')


FFMPEG_BIN = shutil.which('ffmpeg') is not None
FFMPEG_PYTHON = importlib.util.find_spec('ffmpeg') is not None
WIKI_API = importlib.util.find_spec('wikipediaapi') is not None

PLY_ID_PREF   = 'pl-'

BEETS_MULTI_DELIM = '\\\u2400'  # what's used in beets' db to separate multiple artists, multiple genres etc
GENRES_DELIM = re.compile('|'.join([';', ',', '/', '\\|', '\u2400', '\\', '\x00']))

ASCII_TRANSLATE_TABLE = {
    ord('\u2010'): '-', ord('\u2011'): '-', ord('\u2012'): '-',
    ord('\u2013'): '-', ord('\u2014'): '-', ord('\u2015'): '-',
    ord('\u2212'): '-', ord('\u2018'): "'", ord('\u2019'): "'",
    ord('\u201a'): "'", ord('\u201b'): "'", ord('\u201c'): '"',
    ord('\u201d'): '"', ord('\u201e'): '"', ord('\u201f'): '"',
    ord('\u00a0'): ' ', ord('\u2000'): ' ', ord('\u2001'): ' ',
    ord('\u2002'): ' ', ord('\u2003'): ' ', ord('\u2004'): ' ',
    ord('\u2005'): ' ', ord('\u2006'): ' ', ord('\u2007'): ' ',
    ord('\u2008'): ' ', ord('\u2009'): ' ', ord('\u200a'): ' ',
    ord('\u202f'): ' ', ord('\u2026'): '...',
}

ALPHANUM_CHARS = re.compile(r'^[a-zA-Z0-9_]+$')

PROJECT_ROOT = Path(os.path.abspath(__file__)).parent

def cache_location() -> Path:
    if platform.system() == "Windows":
        cache_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif platform.system() == "Darwin":
        cache_dir = Path.home() / "Library" / "Caches"
    else:
        cache_dir = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

    final_path = cache_dir / "beetstreamnext"
    final_path.mkdir(parents=True, exist_ok=True)
    return final_path

CACHE_LOCATION = cache_location()

RATE_LIMIT_MAX_FAILURES = 5
RATE_LIMIT_BLOCK_WINDOW = 300

NOW_PLAYING_TIMEOUT_SEC = 600   # stale after 10 min
CLEANUP_INTERVAL_SEC = 86400    # clean once per day
MAX_CACHE_AGE_DAYS = 30
SESSION_KEY_ROTATION_DAYS = 30

LOOPBACK_IPS = frozenset({'127.0.0.1', 'localhost', '::1'})

EXISTING_USER_FIELDS = frozenset({
    'username', 'password', 'email', 'avatar', 'avatarLastChanged', 'scrobblingEnabled', 'adminRole', 'settingsRole',
    'streamRole', 'jukeboxRole', 'downloadRole', 'uploadRole', 'coverArtRole', 'playlistRole', 'commentRole',
    'podcastRole', 'shareRole', 'videoConversionRole', 'folder', 'maxBitRate'
})
ALLOWED_BITRATES = frozenset({0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320})

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.webp', '.bmp'}
ALLOWED_THUMBNAIL_SIZES = [56, 120, 250, 500, 1000, 1200]