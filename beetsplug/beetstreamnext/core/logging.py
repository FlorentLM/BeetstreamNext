import re
import logging
import collections
from typing import List
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from paste.translogger import TransLogger


_werkzeug_regex = re.compile(r'("(?:GET|POST|PUT|DELETE|HEAD|PATCH|OPTIONS) )(\S+)( HTTP/\d)')

_SENSITIVE_URL_PARAMS = frozenset({'p', 't', 's', 'apiKey'})
_REDACTED = 'REDACTED'


# LOG_LEVEL = logging.ERROR
LOG_LEVEL = logging.INFO
# LOG_LEVEL = logging.DEBUG

DISABLE_LOGS_REDACTION = False
# DISABLE_LOGS_REDACTION = True


logging.basicConfig(encoding='utf-8', level=LOG_LEVEL)


def _scrub_uri(req_uri: str) -> str:
    """Replace sensitive query-param values with a placeholder, keep everything else."""
    try:
        parts = urlsplit(req_uri)
    except ValueError:
        return req_uri
    if not parts.query:
        return req_uri

    scrubbed = [
        (k, _REDACTED if k in _SENSITIVE_URL_PARAMS else v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    ]
    new_query = urlencode(scrubbed)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


class RedactingTransLogger(TransLogger):
    """
    TransLogger that redacts credential query params.
    """

    def write_log(self, environ, method, req_uri, start, status, bytes):

        if DISABLE_LOGS_REDACTION:
            super().write_log(environ, method, req_uri, start, status, bytes)

        else:
            try:
                referer = environ.get('HTTP_REFERER')
                if referer and referer != '-' and '?' in referer:
                    environ = dict(environ)
                    environ['HTTP_REFERER'] = _scrub_uri(referer)
                req_uri = _scrub_uri(req_uri)
            except Exception:
                pass
            super().write_log(environ, method, req_uri, start, status, bytes)


class LoggingRedactingFilter(logging.Filter):
    """
    Logging filter that scrubs sensitive query params from any log whose message contains a URL.
    """
    def filter(self, record: logging.LogRecord) -> bool:

        if DISABLE_LOGS_REDACTION:
            return True

        else:
            msg = record.getMessage()
            if '?' in msg and any(f'{p}=' in msg for p in _SENSITIVE_URL_PARAMS):
                record.msg = self._scrub_werkzeug_line(msg)
                record.args = ()
            return True

    @staticmethod
    def _scrub_werkzeug_line(line: str) -> str:
        """
        Werkzeug logs lines like: "GET /rest/ping?u=x&p=y HTTP/1.1" 200 -
        Try (best effort) to find the quoted request target and scrub its query string.
        """

        def _repl(m: re.Match) -> str:
            return m.group(1) + _scrub_uri(m.group(2)) + m.group(3)

        # "METHOD URI HTTP/x.y"  -> capture the URI between method and protocol
        return re.sub(_werkzeug_regex, _repl, line)


def apply_logs_redaction() -> None:
    """
    Attach the redacting filter to the loggers whose records can contain raw request URLs.
    """
    if DISABLE_LOGS_REDACTION:
        return
    _filter = LoggingRedactingFilter()
    logging.getLogger('werkzeug').addFilter(_filter)
    bsn_logger.addFilter(_filter)


class MemLogBuffer(logging.Handler):
    """Ring buffer to keep the last n log lines in memory (for the admin panel)."""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self.buffer: collections.deque = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(self.format(record))
        except Exception:
            self.handleError(record)

    @property
    def recents(self) -> List[str]:
        return list(self.buffer)


##
# Setup

logging.getLogger('flask').setLevel(LOG_LEVEL)
logging.getLogger('werkzeug').setLevel(LOG_LEVEL)

bsn_logger = logging.getLogger('beetstreamnext')
bsn_logger.propagate = True

mem_log = MemLogBuffer()

mem_log.setFormatter(logging.Formatter('%(asctime)s %(levelname)-8s %(name)s: %(message)s'))
logging.getLogger().addHandler(mem_log)