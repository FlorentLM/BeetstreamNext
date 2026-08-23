import platform
from pathlib import Path
from typing import Optional, Dict, Tuple, Any
from functools import lru_cache
from datetime import datetime, timezone
import beets
import flask

from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.utils.system import get_mimetype
from beetsplug.beetstreamnext.utils.text import remove_accents, split_beets_multi, customstrip, standard_ascii, safe_str
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import GENRES_DELIM, BEETSTREAMNEXT_VER


##
# General helpers

def grab_auth_params() -> Dict[str, str]:
    r = flask.request.values

    auth_params = {k: r.get(k, default='', type=str) for k in ['s', 't', 'p', 'apiKey'] if k in r}
    other_auth_params = {k: r.get(k, default='', type=safe_str) for k in ['u', 'c', 'v'] if k in r}
    auth_params.update(other_auth_params)

    return auth_params


def _default_config_path() -> str:
    """Where beets would load its config from when not started with an explicit -c/--config."""
    try:
        return beets.config.user_config_path()
    except Exception:
        return 'default location'


def human_bytes(n: int) -> str:
    size = float(n)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024:
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


def get_server_info(extended: bool = False) -> Dict[str, str]:
    lib = app.config['lib']
    stats = {}
    with lib.transaction() as tx:
        stats['artists'] = tx.query("SELECT COUNT(DISTINCT albumartist) FROM albums")[0][0]
        stats['albums'] = tx.query("SELECT COUNT(*) FROM albums")[0][0]
        stats['songs'] = tx.query("SELECT COUNT(*) FROM items")[0][0]

    if extended:
        additional_info = {
            'version': BEETSTREAMNEXT_VER,
            'beets_version': beets.__version__,
            'python_version': platform.python_version(),
            'os': platform.system(),
            'db_path': str(app.config.get('BSN_DB_PATH')),
            'library_path': str(app.config.get('BEETS_DB_PATH')),
            'config_path': str(app.config.get('BEETS_CONFIG_PATH')) if app.config.get('BEETS_CONFIG_PATH') else _default_config_path(),
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


@lru_cache(maxsize=1024)
def genres_formatter(genres: Optional[str]) -> Tuple[str, ...]:
    """Additional cleaning for common genres formatting issues."""
    if not genres:
        return ()

    raw_list = split_beets_multi(genres)
    separated = GENRES_DELIM.split(';'.join(raw_list))

    cleaned = []
    for g in separated:
        tag = standard_ascii(g).title()

        tag = (tag.replace('Post ', 'Post-')
               .replace('Prog ', 'Progressive ')
               .replace('Rnb', 'R&B')
               .replace("R'N'B", 'R&B')
               .replace("R 'N' B", 'R&B')
               .replace('Rock & ', 'Rock and ')
               .replace("Rock'N'", 'Rock and')
               .replace("Rock 'N'", 'Rock and')
               .replace('.', ' '))

        final_tag = customstrip(tag, punctuation=True)
        final_tag = remove_accents(final_tag)
        if final_tag and final_tag not in cleaned:
            cleaned.append(final_tag)

    return tuple(cleaned)


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
