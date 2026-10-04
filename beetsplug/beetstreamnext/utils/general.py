from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Optional, Tuple

from beetsplug.beetstreamnext.utils.text import split_beets_multi, strip_text, standard_ascii, safe_str
from beetsplug.beetstreamnext.constants import (
    GENRE_MAP, GENRES_REGEX, GENRE_TOKEN_MAP, COLLAPSE_SPACES, DOT_TRANS, DECADE_APOSTROPHE, GENRES_DELIM
)


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
