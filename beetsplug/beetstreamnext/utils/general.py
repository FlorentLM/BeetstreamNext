import platform
from typing import Optional, Dict, Tuple, Any
from functools import lru_cache
from datetime import datetime, timezone
import beets
import flask

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
            'db_path': str(app.config.get('DB_PATH')),
            'library_path': str(app.config.get('BEETS_DB_PATH')),
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