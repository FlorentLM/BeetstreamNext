from __future__ import annotations
import re
import sys
import textwrap
from typing import Dict, List, Optional


class TermColors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    REVERSE = "\033[;7m"
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def _wrap(text: str, width: int, initial_indent: str = '', subsequent_indent: str = '') -> List[str]:
    return textwrap.wrap(
        text, width=width,
        initial_indent=initial_indent, subsequent_indent=subsequent_indent,
        break_long_words=False, break_on_hyphens=False,
    )


def wrap_bullet(label: str, items: List[str], width: int = 68) -> List[str]:
    """
    Wrap a comma-separated item list under a ' ▶ ' bullet to fit print_box's width.
    Continuation lines are indent-only.
    """
    wrapped = _wrap(
        ', '.join(items), width,
        initial_indent=f'  ▶  {label}',
        subsequent_indent='     ',
    )
    return wrapped or [f'  ▶  {label}']


def drift_lines(drift: Dict[str, dict]) -> List[str]:
    lines = []
    for table, info in drift.items():
        if 'unknown_migrations' in info:
            lines.extend(wrap_bullet(f'{table}: unrecognized migration(s) — ', info['unknown_migrations']))
        else:
            if info['unknown_columns']:
                lines.extend(wrap_bullet(f'{table}: unrecognized column(s) — ', info['unknown_columns']))
            if info['missing_columns']:
                lines.extend(wrap_bullet(f'{table}: missing expected column(s) — ', info['missing_columns']))
    return lines


def print_box(lines: list[str], width: int = 68, color: Optional[str] = None) -> None:
    col = color if color else ''
    border = '═' * width
    print(f'\n{col}╔{border}╗{TermColors.ENDC}')
    for line in lines:
        true_len = len(TermColors.ansi_escape.sub('', line))
        indented = line.startswith('  ')
        if true_len <= width or true_len != len(line):
            # short enough, or carries ANSI codes (headers): print as-is, don't wrap
            sub_lines = [line]
        else:
            sub_lines = _wrap(line, width) or ['']
        for sub in sub_lines:
            true_len = len(TermColors.ansi_escape.sub('', sub))
            w = width + (len(sub) - true_len)
            to_print = f'{sub:<{w}}' if indented else sub.center(w, ' ')
            print(f'{col}║{TermColors.ENDC}{to_print}{col}║{TermColors.ENDC}')
    print(f'{col}╚{border}╝{TermColors.ENDC}\n')
    sys.stdout.flush()  # stdout is fully-buffered when not a TTY (docker logs, etc) without this
