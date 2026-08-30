import secrets
import threading
from typing import Dict, Optional


class Tokeniser:
    """
    Maps opaque tokens to arbitrary string payloads (filesystem path, encoded artist/img size
    pair, etc).
    Same trust model as public share links.

    Registering the same payload twice returns the same token, so URLs built from
    it stay stable across requests.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._by_token: Dict[str, str] = {}
        self._by_payload: Dict[str, str] = {}

    def register(self, payload: str) -> str:
        with self._lock:
            token = self._by_payload.get(payload)
            if token is None:
                token = secrets.token_urlsafe(16)
                self._by_token[token] = payload
                self._by_payload[payload] = token
            return token

    def resolve(self, token: str) -> Optional[str]:
        with self._lock:
            return self._by_token.get(token)

    def clear(self) -> None:
        with self._lock:
            self._by_token.clear()
            self._by_payload.clear()


##

stream_tokeniser = Tokeniser()          # per-track URLs -> filesystem paths (Sonos jukebox)
image_tokeniser = Tokeniser()           # tokenised-image URLs -> "<artist/album id>|<size>" (get{Artist,Album}Info(2), ArtistID3.artistImageUrl)
