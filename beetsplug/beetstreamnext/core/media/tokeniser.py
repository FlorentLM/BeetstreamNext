from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from typing import Callable, Optional, Tuple

from beetsplug.beetstreamnext.constants import (
    IMAGE_TOKEN_MAX, IMAGE_TOKEN_TTL_SEC, STREAM_TOKEN_MAX, STREAM_TOKEN_TTL_SEC
)


class Tokeniser:
    """
    Maps opaque tokens to arbitrary string payloads (filesystem path, encoded artist/img size
    pair, etc).
    Same trust model as public share links except tokens don't live forever:

    - a token expires `ttl` seconds after it was last created or used
    - maximum `max_entries` tokens are kept (the ones least recently used are dropped first)

    Registering the same payload twice returns the same token, so URLs built from
    it stay stable across requests.
    """

    def __init__(self,
            ttl: float = STREAM_TOKEN_TTL_SEC,
            max_entries: int = STREAM_TOKEN_MAX,
            clock: Callable[[], float] = time.monotonic
        ):

        self._ttl = ttl
        self._max = max_entries
        self._clock = clock
        self._lock = threading.Lock()

        # token -> (payload, expires_at)
        self._by_token: OrderedDict[str, Tuple[str, float]] = OrderedDict()
        self._by_payload: dict[str, str] = {}

    def _drop(self, token: str) -> None:
        payload, _ = self._by_token.pop(token)
        self._by_payload.pop(payload, None)

    def _evict(self, now: float) -> None:

        while self._by_token:
            token, (_, expires) = next(iter(self._by_token.items()))
            if expires > now and len(self._by_token) <= self._max:
                break

            self._drop(token)

    def register(self, payload: str) -> str:
        with self._lock:
            now = self._clock()
            self._evict(now)

            token = self._by_payload.get(payload)
            if token is None:
                token = secrets.token_urlsafe(16)
                self._by_payload[payload] = token
                self._by_token[token] = (payload, now + self._ttl)
                self._evict(now)

            else:
                self._by_token[token] = (payload, now + self._ttl)
                self._by_token.move_to_end(token)
            return token

    def resolve(self, token: str) -> Optional[str]:

        with self._lock:
            entry = self._by_token.get(token)
            if entry is None:
                return None

            payload, expires = entry
            now = self._clock()
            if expires <= now:
                self._drop(token)
                return None

            self._by_token[token] = (payload, now + self._ttl)
            self._by_token.move_to_end(token)
            return payload

    def clear(self) -> None:
        with self._lock:
            self._by_token.clear()
            self._by_payload.clear()


##

stream_tokeniser = Tokeniser(STREAM_TOKEN_TTL_SEC, STREAM_TOKEN_MAX)       # per-track URLs -> filesystem paths (Sonos jukebox)
image_tokeniser = Tokeniser(IMAGE_TOKEN_TTL_SEC, IMAGE_TOKEN_MAX)          # tokenised-image URLs -> "<artist/album id>|<size>" (get{Artist,Album}Info(2), ArtistID3.artistImageUrl)
