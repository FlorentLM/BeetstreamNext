import os
import re
import shutil
from pathlib import Path
from typing import Dict

from beetsplug.beetstreamnext.utils.system import is_installed, cache_location


## Versions

REPO_URL: str = 'https://github.com/FlorentLM/BeetstreamNext'
SUBSONIC_API_VER: str = '1.16.1'
BEETSTREAMNEXT_VER: str = '1.6.0-dev'

USER_AGENT: str = f'BeetstreamNext/{BEETSTREAMNEXT_VER} ( {REPO_URL} )'

## Paths and deps

FFMPEG_BIN: bool = shutil.which('ffmpeg') is not None
FFMPEG_PYTHON: bool = is_installed('ffmpeg-python')
WIKI_API: bool = is_installed('wikipedia-api')
RADIO_BROWSER: bool = is_installed('radios')

PROJECT_ROOT: Path = Path(os.path.abspath(__file__)).parent
CACHE_LOCATION: Path = cache_location()


## Text constants

BEETS_MULTI_DELIM: str = '\\\u2400'  # what's used in beets' db to separate multiple artists, multiple genres etc
GENRES_DELIM: re.Pattern = re.compile('|'.join([';', ',', '/', '\\|', '\u2400', '\\', '\x00']))

ASCII_TRANSLATE_TABLE: Dict[int, str] = {
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

ALPHANUM_CHARS: re.Pattern = re.compile(r'^[a-zA-Z0-9_]+$')


## Security

LOOPBACK_IPS: frozenset[str] = frozenset({'127.0.0.1', 'localhost', '::1'})

RATE_LIMIT_MAX_FAILURES: int = 5
RATE_LIMIT_BLOCK_WINDOW: int = 300

MIN_PASSWORD_LEN: int = 8


## Maintenance timings

NOW_PLAYING_TIMEOUT_SEC: int = 600   # stale after 10 min
CLEANUP_INTERVAL_SEC: int = 86400    # clean once per day
MAX_CACHE_AGE_DAYS: int = 30
SESSION_KEY_ROTATION_DAYS: int = 30


## Images

MAX_DECODE_PIXELS: int = 64 * 1024 * 1024    # 64 megapixels decode cap, ~8000x8000 px
MAX_REMOTE_IMAGE_BYTES: int = 15 * 1024 * 1024

MAX_AVATAR_DIM: int = 512
MAX_AVATAR_BYTES: int = 1 * 1024 * 1024      # 1 MB