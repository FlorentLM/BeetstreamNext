import secrets
import threading
import time
from typing import Any, Optional


class TemporaryStore:
    """
    Thread-safe, in-memory, single-read store with TTL for handing a freshly-generated
    secret (e.g. a raw API key) from the POST that created it to the very next page render.
    """

    def __init__(self, ttl_seconds: int = 120):
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}

    def _purge_expired(self, now: float) -> None:
        stale = [k for k, (exp, _) in self._store.items() if exp <= now]
        for k in stale:
            del self._store[k]

    def put(self, value: Any) -> str:
        """Store value, return a claim token."""
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            self._store[token] = (now + self._ttl, value)
        return token

    def claim(self, token: Optional[str]) -> Optional[Any]:
        """Return the value for token, only once, then delete it. None if absent/expired."""
        if not token:
            return None
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            entry = self._store.pop(token, None)
        if entry is None:
            return None
        exp, value = entry
        return value if exp > now else None


temporary_store = TemporaryStore(ttl_seconds=120)