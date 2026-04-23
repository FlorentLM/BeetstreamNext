import re
from typing import Optional


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


def print_box(lines: list[str], width: int = 68, color: Optional[str] = None) -> None:
    col = color if color else ''
    border = '═' * width
    print(f'\n{col}╔{border}╗{TermColors.ENDC}')
    for line in lines:
        true_len = len(TermColors.ansi_escape.sub('', line))
        w = width + (len(line) - true_len)
        to_print = f'{line:<{w}}' if line.startswith('  ▶') else line.center(w, ' ')
        print(f'{col}║{TermColors.ENDC}{to_print}{col}║{TermColors.ENDC}')
    print(f'{col}╚{border}╝{TermColors.ENDC}\n')
