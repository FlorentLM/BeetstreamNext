from __future__ import annotations

import json
import queue
import threading
from contextlib import contextmanager
from typing import Any, Iterator


class EventBus:
    """
    Mini pub/sub bus to push live updates to connected admin sessions.
    """

    def __init__(self, queue_size: int = 100) -> None:
        self._queue_size = queue_size
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()

    @contextmanager
    def subscribe(self) -> Iterator[queue.Queue]:
        q: queue.Queue = queue.Queue(maxsize=self._queue_size)

        with self._lock:
            self._subscribers.add(q)
        try:
            yield q
        finally:
            with self._lock:
                self._subscribers.discard(q)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def has_subscribers(self) -> bool:
        """
        Lets publishers skip building payloads if nobody's listening.
        """
        with self._lock:
            return bool(self._subscribers)

    def publish(self, event: str, data: Any) -> None:
        """
        Push an event to every connected admin session.
        """
        payload = data if isinstance(data, str) else json.dumps(data)

        with self._lock:
            subscribers = list(self._subscribers)

        for q in subscribers:
            try:
                q.put_nowait((event, payload))
            except queue.Full:
                # Slow consumer? -> Drop oldest queued message and dont block
                try:
                    q.get_nowait()
                    q.put_nowait((event, payload))
                except (queue.Empty, queue.Full):
                    pass


admin_events = EventBus()
