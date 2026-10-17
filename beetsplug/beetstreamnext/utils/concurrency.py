from __future__ import annotations

import queue
import threading
import time
from collections import Counter, defaultdict, deque
from typing import Callable, Hashable, Optional


class WorkerPool:
    """
    Run submitted jobs on max `size` daemon threads, queue extra jobs.
    """

    def __init__(self, size: int, name: str = 'worker') -> None:
        self._size = size
        self._name = name

        self._jobs: queue.Queue = queue.Queue()

        self._lock = threading.Lock()

        self._threads: list[threading.Thread] = []

    def submit(self, fn: Callable, *args) -> None:

        self._jobs.put((fn, args))

        with self._lock:
            if len(self._threads) < self._size:

                thread = threading.Thread(
                    target=self._run,
                    name=f'{self._name}-{len(self._threads)}',
                    daemon=True
                )
                self._threads.append(thread)
                thread.start()

    def _run(self) -> None:
        while True:
            fn, args = self._jobs.get()
            try:
                fn(*args)
            except Exception:
                pass    # Jobs shoudl report their own errors
            finally:
                self._jobs.task_done()


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


class RollingLimiter:
    """Lets each key through at most `max_events` times in any `window` seconds."""

    def __init__(self, max_events: int, window: float) -> None:
        self._max_events = max_events
        self._window = window
        self._lock = threading.Lock()
        self._events: defaultdict[Hashable, deque] = defaultdict(deque)

    def allow(self, key: Hashable, now: Optional[float] = None) -> bool:

        now = time.time() if now is None else now

        with self._lock:
            events = self._events[key]
            while events and now - events[0] > self._window:
                events.popleft()

            if len(events) >= self._max_events:
                return False

            events.append(now)
            return True


class KeyedLimiter:
    """Caps how many things each key can have running at once."""

    def __init__(self, max_per_key: int) -> None:
        self._max = max_per_key
        self._lock = threading.Lock()
        self._held: Counter = Counter()

    def try_acquire(self, key: Optional[Hashable]) -> bool:
        if not key:
            return True
        with self._lock:
            if self._held[key] >= self._max:
                return False
            self._held[key] += 1
            return True

    def release(self, key: Optional[Hashable]) -> None:
        if not key:
            return
        with self._lock:
            self._held[key] -= 1
            if self._held[key] <= 0:
                del self._held[key]

    def held(self, key: Hashable) -> int:
        with self._lock:
            return self._held.get(key, 0)


class DoOnce:
    """Hands out a single True from `claim()`, to do something (like warn) only the first time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claimed = False

    def claim(self) -> bool:
        with self._lock:
            if self._claimed:
                return False
            self._claimed = True
            return True
