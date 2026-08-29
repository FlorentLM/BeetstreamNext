import time
import os
import re
import shutil
from pathlib import Path
from typing import Dict, Optional

from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.utils.system import is_installed, cache_location


START_TIME = time.time()    # not a constant but...yeah

## Versions

SERVER_NAME: str = 'BeetstreamNext'

REPO_URL: str = f'https://github.com/FlorentLM/BeetstreamNext'
SUBSONIC_API_VER: str = '1.16.1'
SERVER_VERSION: str = '1.8.0'

USER_AGENT: str = f'{SERVER_NAME}/{SERVER_VERSION} ( {REPO_URL} )'

## Paths and deps

FFMPEG_PYTHON: bool = is_installed('ffmpeg-python')
WIKI_API: bool = is_installed('wikipedia-api')
RADIO_BROWSER: bool = is_installed('radios')
FEEDPARSER: bool = is_installed('feedparser')

PROJECT_ROOT: Path = Path(os.path.abspath(__file__)).parent
CACHE_LOCATION: Path = cache_location()

HLS_CACHE_DIR = CACHE_LOCATION / 'hls'
HLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

ZIP_CACHE_DIR = CACHE_LOCATION / 'zips'
ZIP_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def find_ffmpeg() -> Optional[str]:
    from beetsplug.beetstreamnext.settings import settings_store
    custom = settings_store.get('ffmpeg_path')
    found = shutil.which(custom) if custom else shutil.which('ffmpeg')
    if found:
        bsn_logger.info(f'ffmpeg found at: {found}')
    return found


def find_mpv() -> Optional[str]:
    from beetsplug.beetstreamnext.settings import settings_store
    custom = settings_store.get('mpv_path')
    found = shutil.which(custom) if custom else shutil.which('mpv')
    if found:
        bsn_logger.info(f'MPV path: {found}')
    return found


## Text constants

BEETS_MULTI_DELIM: str = '\\\u2400'  # what's used in beets' db to separate multiple artists, multiple genres etc
GENRES_DELIM: re.Pattern = re.compile('|'.join(re.escape(d) for d in [';', ',', '/', '|', '\u2400', '\\', '\x00']))

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

MBID_VALIDATOR = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')

## Security

LOOPBACK_IPS: frozenset[str] = frozenset({'127.0.0.1', 'localhost', '::1'})

RATE_LIMIT_MAX_FAILURES: int = 5
RATE_LIMIT_BLOCK_WINDOW: int = 300

# Second limiter keyed on IP alone (for repeat offenders
# spraying multiple usernames from the same address)
RATE_LIMIT_IP_MAX_FAILURES: int = 20
RATE_LIMIT_IP_BLOCK_WINDOW: int = 3600

MIN_PASSWORD_LEN: int = 8


## URLs cleanup constants

_SCHEME_RE = re.compile(r'^https?://', re.IGNORECASE)
_DUPLICATE_SCHEME_RE = re.compile(r'^(?:https?://)+(?=https?://)', re.IGNORECASE)


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

MAX_PODCAST_IMAGE_DIM: int = 1024


## Podcasts

MAX_PODCAST_FEED_BYTES: int = 30 * 1024 * 1024   # 30 MB cap on a fetched podcast rss feed body