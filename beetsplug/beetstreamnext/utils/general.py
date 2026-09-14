from __future__ import annotations
import platform
import time
from pathlib import Path
from typing import Optional, Dict, Tuple, Any
from functools import lru_cache
from datetime import datetime, timezone
import beets
import flask
import re

from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.utils.system import get_mimetype, find_ffmpeg, find_mpv, binary_version
from beetsplug.beetstreamnext.utils.text import split_beets_multi, customstrip, standard_ascii, safe_str
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import (
    GENRE_MAP, GENRES_REGEX, GENRE_TOKEN_MAP, COLLAPSE_SPACES, DOT_TRANS, DECADE_APOSTROPHE,
    START_TIME, GENRES_DELIM, SERVER_VERSION
)


##
# General helpers

def external_url(path_part: str) -> str:
    """
    Build an absolute URL for 'path_part' with external hostname taking precedence.
    """
    from beetsplug.beetstreamnext.settings import settings_store
    from beetsplug.beetstreamnext.core.security import parse_host

    external_host = settings_store.get('external_hostname')
    if not external_host:
        return flask.request.host_url.rstrip('/') + path_part

    reverse_proxy = settings_store.get('reverse_proxy')
    scheme = 'https' if (flask.request.is_secure or reverse_proxy) else 'http'

    if not reverse_proxy and parse_host(external_host).port is None:
        external_host = f'{external_host}:{settings_store.get("port")}'

    return f'{scheme}://{external_host}{path_part}'


def human_bytes(n: int) -> str:
    size = float(n)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024:
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


def human_time(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    if days:
        return f'{days}d {hours}h {minutes}m'
    if hours:
        return f'{hours}h {minutes}m'
    if minutes:
        return f'{minutes}m {secs}s'
    return f'{secs}s'


def get_server_info(extended: bool = False) -> Dict[str, str]:
    lib = app.config['lib']
    stats = {}
    with lib.transaction() as tx:
        stats['artists'] = tx.query("SELECT COUNT(DISTINCT albumartist) FROM albums")[0][0]
        stats['albums'] = tx.query("SELECT COUNT(*) FROM albums")[0][0]
        stats['songs'] = tx.query("SELECT COUNT(*) FROM items")[0][0]

    if extended:
        ffmpeg_path = find_ffmpeg()
        mpv_path = find_mpv()

        from beetsplug.beetstreamnext.core.beets_interaction import config_path

        try:
            cfg_path = str(config_path())
        except Exception:
            cfg_path = 'default location'

        additional_info = {
            'version': SERVER_VERSION,
            'beets_version': beets.__version__,
            'python_version': platform.python_version(),
            'os': platform.system(),
            'uptime': human_time(time.time() - START_TIME),
            'db_path': str(app.config.get('BSN_DB_PATH')),
            'library_path': str(app.config.get('BEETS_DB_PATH')),
            'config_path': cfg_path,
            'ffmpeg_path': ffmpeg_path or 'not found',
            'ffmpeg_version': (binary_version(ffmpeg_path, '-version') or 'unknown') if ffmpeg_path else None,
            'mpv_path': mpv_path or 'not found',
            'mpv_version': (binary_version(mpv_path, '--version') or 'unknown') if mpv_path else None,
            'stats': stats,
        }
        stats.update(additional_info)

    return stats


##
# Various parsers / converters / formatters


def api_bool(val: Any) -> bool:
    if val is None:
        return False
    return safe_str(val).lower() not in ('false', '0', 'no', 'none', 'null', '')


def timestamp_to_iso(timestamp) -> str:
    if not timestamp or timestamp == 0:
        return ''
    try:
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat().replace('+00:00', 'Z')
    except (ValueError, TypeError):
        return ''


@lru_cache(maxsize=4096)
def genres_formatter(genres: Optional[str]) -> Tuple[str, ...]:
    """Additional cleaning for common genres formatting issues."""

    if not genres:
        return ()

    raw_list = split_beets_multi(genres)
    split_tags = (
        sub_tag
        for raw in raw_list
        for sub_tag in GENRES_DELIM.split(raw)
    )

    def _token_sub(match: re.Match) -> str:
        return GENRE_TOKEN_MAP[match.lastgroup]

    cleaned = {}

    for g in split_tags:
        tag = customstrip(standard_ascii(g), punctuation=True).strip()
        if not tag:
            continue

        if '.' in tag:
            tag = COLLAPSE_SPACES.sub(' ', tag.translate(DOT_TRANS)).strip()

        tag_lower = tag.lower()

        if tag_lower in GENRE_MAP:
            cleaned[GENRE_MAP[tag_lower]] = None
            continue

        tag_titled = tag.title()
        tag_titled = DECADE_APOSTROPHE.sub(lambda m: f"{m.group(1)}'{m.group(2).lower()}", tag_titled)

        processed_tag = GENRES_REGEX.sub(_token_sub, tag_titled).strip()

        if processed_tag:
            cleaned[processed_tag] = None

    return tuple(cleaned.keys())


def _sendfile_offload(file_path: Path, as_attachment: bool, download_name: Optional[str]) -> flask.Response | None:
    """
    Hand the file off to the reverse proxy (Nginx/Apache) instead of streaming it through
    Python (if configured). Returns None if offloading isn't enabled/possible.
    """
    from beetsplug.beetstreamnext.settings import settings_store

    if not settings_store.get('reverse_proxy'):
        return None

    method = settings_store.get('sendfile_method')
    if method == 'off':
        return None

    resp = flask.Response(status=200, mimetype=get_mimetype(file_path))
    if as_attachment:
        resp.headers['Content-Disposition'] = f'attachment; filename="{download_name or file_path.name}"'

    if method == 'x-sendfile':
        resp.headers['X-Sendfile'] = str(file_path)
        return resp

    if method == 'x-accel-redirect':
        root = Path(app.config['root_directory'])
        try:
            rel = file_path.resolve().relative_to(root.resolve())
        except ValueError:
            bsn_logger.warning(f"'{file_path}' is outside root_directory, can't use X-Accel-Redirect.")
            return None
        prefix = settings_store.get('sendfile_internal_prefix').rstrip('/')
        resp.headers['X-Accel-Redirect'] = f'{prefix}/{rel.as_posix()}'
        return resp

    return None


def send_file(
        file_path: str | Path,
        as_attachment: bool = False,
        download_name: Optional[str] = None
    ) -> flask.Response | None:

    file_path = Path(file_path)

    offloaded = _sendfile_offload(file_path, as_attachment, download_name)
    if offloaded is not None:
        return offloaded

    try:
        return flask.send_file(
            file_path,
            mimetype=get_mimetype(file_path),
            as_attachment=as_attachment,
            download_name=download_name
        )
    except OSError as e:
        bsn_logger.error(f"Failed to serve file '{file_path}': {e}")
        return None
