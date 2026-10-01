from __future__ import annotations

import html
import re
from typing import List, Optional


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


_ANSI_SEQUENCE = re.compile(r'(\x1b\[[0-9;]*[a-zA-Z])')
_ANSI_COLOR_CLASSES = {
    **{30 + i: f'ansi-fg-{name}' for i, name in enumerate(
        ('black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'))},
    **{90 + i: f'ansi-fg-bright-{name}' for i, name in enumerate(
        ('black', 'red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white'))},
}


def ansi_to_html(line: str) -> str:
    """
    HTML-escape a log line and turn ANSI colour/style codes into <span class="ansi-...">
    """

    out: List[str] = []
    span_open = False
    color: Optional[str] = None
    flags = {'bold': False, 'dim': False, 'italic': False, 'underline': False}
    flag_codes = {1: ('bold', True), 2: ('dim', True), 3: ('italic', True), 4: ('underline', True),
                  23: ('italic', False), 24: ('underline', False)}

    for part in _ANSI_SEQUENCE.split(line):
        m = re.fullmatch(r'\x1b\[([0-9;]*)([a-zA-Z])', part)
        if not m:
            if part:
                out.append(html.escape(part, quote=False))
            continue
        if m.group(2) != 'm':
            continue   # not a colour/style code

        if span_open:
            out.append('</span>')
            span_open = False

        for code in ([int(c) for c in m.group(1).split(';') if c] or [0]):
            if code == 0:
                color = None
                flags = dict.fromkeys(flags, False)
            elif code == 22:
                flags['bold'] = flags['dim'] = False
            elif code == 39:
                color = None
            elif code in flag_codes:
                key, value = flag_codes[code]
                flags[key] = value
            elif code in _ANSI_COLOR_CLASSES:
                color = _ANSI_COLOR_CLASSES[code]

        classes = ([color] if color else []) + [f'ansi-{k}' for k, on in flags.items() if on]
        if classes:
            out.append(f'<span class="{" ".join(classes)}">')
            span_open = True

    if span_open:
        out.append('</span>')
    return ''.join(out)
