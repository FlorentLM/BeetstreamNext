from __future__ import annotations

import re
import logging
import collections
import threading
from typing import Optional, List
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from paste.translogger import TransLogger

from beetsplug.beetstreamnext.core.services.events import admin_events
from beetsplug.beetstreamnext.utils.ansi import log_line


# Matched case-insensitively
_SENSITIVE_URL_PARAMS = frozenset({
    'p', 't', 's', 'apikey', 'api_key', 'password', 'token', 'access_token', 'secret', 'auth'
})
_sensitive_param_regex = re.compile(
    r'([?&;](?:' + '|'.join(sorted(map(re.escape, _SENSITIVE_URL_PARAMS), key=len, reverse=True)) + r')=)[^&\s"\'#]*',
    re.IGNORECASE
)
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
        (k, _REDACTED if k.lower() in _SENSITIVE_URL_PARAMS else v)
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

        msg = record.getMessage()
        if '=' in msg:
            scrubbed = self._scrub_text(msg)
            if scrubbed != msg:
                record.msg = scrubbed
                record.args = ()
        return True

    @staticmethod
    def _scrub_text(text: str) -> str:
        """
        Blank the value of a sensitive `?param=value` / `&param=value` found anywhere
        in the text (request lines, referrers, URLs in error messages from requests/urllib3 etc).
        """
        return _sensitive_param_regex.sub(lambda m: m.group(1) + _REDACTED, text)


def apply_logs_redaction() -> None:

    if DISABLE_LOGS_REDACTION:
        return

    _filter = LoggingRedactingFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(_filter)

    logging.getLogger('werkzeug').addFilter(_filter)
    bsn_logger.addFilter(_filter)


class MemLogBuffer(logging.Handler):
    """Ring buffer to keep the last n log lines in memory (for the admin panel)."""

    PUSH_INTERVAL = 0.25  # seconds

    def __init__(self, capacity: int = 2000):
        super().__init__()

        self.buffer: collections.deque = collections.deque(maxlen=capacity)

        self._pending: List[str] = []
        self._pending_lock = threading.Lock()
        self._flush_timer: Optional[threading.Timer] = None

    def _flush(self) -> None:
        with self._pending_lock:
            lines, self._pending = self._pending, []
            self._flush_timer = None

        if lines:
            admin_events.publish('server-log', lines)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            entry = (line, record.levelname, record.name)
            self.buffer.append(entry)

            if admin_events.has_subscribers():
                with self._pending_lock:
                    self._pending.append(log_line(*entry))
                    if self._flush_timer is None:
                        self._flush_timer = threading.Timer(self.PUSH_INTERVAL, self._flush)
                        self._flush_timer.daemon = True
                        self._flush_timer.start()
        except Exception:
            self.handleError(record)

    @property
    def recents(self) -> List[str]:
        return [log_line(*entry) for entry in self.buffer]


class TracebackFilter(logging.Filter):
    """
    Removes full traceback dump from log records.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        record.exc_info = None
        record.exc_text = None
        return True


##
# Setup

logging.getLogger('flask').setLevel(LOG_LEVEL)
logging.getLogger('werkzeug').setLevel(LOG_LEVEL)
logging.getLogger('requests_cache.session').addFilter(TracebackFilter())  # only this one gets the Traceback filter

bsn_logger = logging.getLogger('beetstreamnext')
bsn_logger.propagate = True

mem_log = MemLogBuffer()

logging.getLogger().addHandler(mem_log)
logging.getLogger('wsgi').addHandler(mem_log)