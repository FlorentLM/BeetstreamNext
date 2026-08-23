import string
import unicodedata
from typing import Any, Sequence, List

from beetsplug.beetstreamnext.constants import MBID_VALIDATOR, BEETS_MULTI_DELIM, ASCII_TRANSLATE_TABLE


##
# Text utilities


def validate_mbid(value: Any) -> str:
    """Return value if it looks like a genuine MusicBrainz UUID, else ''."""
    v = str(value or '').strip()
    return v if MBID_VALIDATOR.match(v) else ''


def remove_accents(text: Any) -> str:
    if not text:
        return ''
    return ''.join(c for c in unicodedata.normalize('NFD', str(text)) if unicodedata.category(c) != 'Mn')


def split_beets_multi(stringlist: Sequence[Any] | str) -> List[str]:
    """Split a beets multi-value field."""
    if not stringlist:
        return []

    if not isinstance(stringlist, str) and isinstance(stringlist, Sequence):
        # re-join if it's a sequence
        stringlist = BEETS_MULTI_DELIM.join(stringlist)

    splitted = str(stringlist).split(BEETS_MULTI_DELIM)
    return [s.strip('\\\u2400') for s in splitted if s]


def customstrip(value: Any, punctuation: bool = False) -> str:
    if not value:
        return ''
    if isinstance(value, bytes):
        try:
            s = value.decode('utf-8')
        except UnicodeDecodeError:
            return ''
    else:
        s = str(value)
    to_strip = string.whitespace + '\v\f\x00'
    if punctuation:
        to_strip += string.punctuation

    return s.strip(to_strip)


def standard_ascii(text: Any) -> str:
    """Replace fancy unicode characters by standard ASCII equivalents."""
    if not text:
        return ''
    text = unicodedata.normalize('NFC', str(text))
    return text.translate(ASCII_TRANSLATE_TABLE).strip()


def trim_text(text: str, char_limit: int = 300) -> str:
    if len(text) <= char_limit:
        return text

    snippet = text[:char_limit]
    period_index = text.find(".", char_limit)

    if period_index != -1:
        snippet = text[:period_index + 1]

    return snippet


def format_duration(seconds: Any) -> str:
    """Format a duration in seconds as M:SS, or H:MM:SS."""
    try:
        total = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        return '0:00'

    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)

    if hours:
        return f'{hours}:{minutes:02d}:{secs:02d}'
    return f'{minutes}:{secs:02d}'


def safe_str(val: Any) -> str:
    if val is None:
        return ''
    s = str(val)
    s = unicodedata.normalize('NFC', s)
    s = standard_ascii(s)
    return customstrip(s)
