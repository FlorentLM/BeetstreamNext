from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Collection, Dict, Mapping, Optional, Tuple

from beetsplug.beetstreamnext.utils.text import split_beets_multi, strip_text, standard_ascii, safe_str
from beetsplug.beetstreamnext.constants import (
    GENRE_MAP, GENRES_REGEX, GENRE_TOKEN_MAP, COLLAPSE_SPACES, DOT_TRANS, DECADE_APOSTROPHE, GENRES_DELIM,
    MAX_PAGE_SIZE
)


##
# Various parsers / converters / formatters


def filter_dict(data: Mapping, keep: Optional[Collection] = None, drop: Collection = ()) -> dict:
    """A copy of `data` only with the keys in `keep` and without the keys in `drop`."""
    return {k: v for k, v in data.items() if (keep is None or k in keep) and k not in drop}


@lru_cache(maxsize=1)
def _secret_env_vars() -> frozenset:
    from beetsplug.beetstreamnext.core.config.settings_schema import SETTINGS_SCHEMA

    return frozenset(
        {'BEETSTREAMNEXT_KEY'}
        | {spec['env_var'] for spec in SETTINGS_SCHEMA.values() if spec.get('sensitive') and 'env_var' in spec}
    )


def subprocess_env(*drop: str) -> Dict[str, str]:
    """
    The environment for a child process without any secret or unnecessary env vars.

    Args:
        - drop: more variables to leave out
    """
    return filter_dict(os.environ, drop=_secret_env_vars() | set(drop))



def api_bool(val: Any) -> bool:
    if val is None:
        return False
    return safe_str(val).lower() not in ('false', '0', 'no', 'none', 'null', '')


def page_size(val: Any) -> int:
    return max(0, min(int(val), MAX_PAGE_SIZE))


def page_offset(val: Any) -> int:
    return max(0, int(val))


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
        tag = strip_text(standard_ascii(g), punctuation=True).strip()
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
