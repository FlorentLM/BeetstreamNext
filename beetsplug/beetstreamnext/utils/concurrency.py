from __future__ import annotations

import threading
import time
from typing import Optional


class SingleAccess:
    """Lets one thread at a time do something."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def try_acquire(self) -> bool:
        return self._lock.acquire(blocking=False)

    def release(self) -> None:
        self._lock.release()


class Throttle:
    """Lets something happen on a limited interval."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: Optional[float] = None

    def allow(self, interval: float) -> bool:
        now = time.monotonic()
        with self._lock:
            if interval and self._last is not None and now - self._last < interval:
                return False
            self._last = now
            return True
