"""Synthetic condition-backed inbox with a stale readiness flag."""
from collections import deque
from threading import Condition


class Inbox:
    def __init__(self):
        self._condition = Condition()
        self._items = deque()
        self._ready = False

    def publish(self, value):
        with self._condition:
            self._items.append(value)
            self._condition.notify_all()

    def take(self, timeout=0.25):
        with self._condition:
            if not self._condition.wait_for(lambda: self._ready, timeout):
                raise TimeoutError("inbox-not-ready")
            return self._items.popleft()
