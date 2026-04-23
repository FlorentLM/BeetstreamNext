import os
import re
from pathlib import Path

SUBSONIC_API_VERSION = '1.16.1'
BEETSTREAMNEXT_VERSION = '1.6.0-dev'

ART_ID_PREF   = 'ar-'
ART_MBID_PREF = 'ar-m-'  # ar-m-<base64url(mbid)>  preferred if mbid is known
ART_NAME_PREF = 'ar-n-'  # ar-n-<base64url(name)>  fallback
ALB_ID_PREF   = 'al-'
SNG_ID_PREF   = 'sg-'
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

PROJECT_ROOT = Path(os.path.abspath(__file__)).parent

NOW_PLAYING_TIMEOUT_SEC = 600   # stale after 10 min
CLEANUP_INTERVAL_SEC = 86400    # clean once per day
MAX_CACHE_AGE_DAYS = 30
SESSION_KEY_ROTATION_DAYS = 30

LOOPBACK_IPS = frozenset({'127.0.0.1', 'localhost', '::1'})

ALLOWED_BITRATES = frozenset({0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320})

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.webp', '.bmp'}
ALLOWED_THUMBNAIL_SIZES = [56, 120, 250, 500, 1000, 1200]
