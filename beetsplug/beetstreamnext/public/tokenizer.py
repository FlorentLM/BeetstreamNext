import secrets
import threading
from typing import Dict, Optional


class StreamTokens:
    """
    Maps per-track URLs to filesystem paths using tokens.
    (same trust model as public share links).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._by_token: Dict[str, str] = {}
        self._by_path: Dict[str, str] = {}

    def register(self, path: str) -> str:
        with self._lock:
            token = self._by_path.get(path)
            if token is None:
                token = secrets.token_urlsafe(16)
                self._by_token[token] = path
                self._by_path[path] = token
            return token

    def resolve(self, token: str) -> Optional[str]:
        with self._lock:
            return self._by_token.get(token)

    def clear(self) -> None:
        with self._lock:
            self._by_token.clear()
            self._by_path.clear()


##

stream_tokens = StreamTokens()
