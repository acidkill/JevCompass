"""Synthetic bounded worker queue with deliberately crossed notifications."""
from collections import deque
from dataclasses import dataclass
from threading import Condition, Event, Lock
from time import monotonic


@dataclass(frozen=True)
class QueueTrace:
    progress_observed: bool
    resource_contention_observed: bool
    wait_condition_satisfiable: bool | None


class WorkerQueue:
    def __init__(self, capacity=1):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("capacity-must-be-positive-int")
        self._capacity = capacity
        self._mutex = Lock()
        self._not_empty = Condition(self._mutex)
        self._not_full = Condition(self._mutex)
        self._items = deque()
        self._progress_count = 0
        self._contention_observed = False
        self._consumer_waiting = Event()
        self._producer_waiting = Event()

    @staticmethod
    def _deadline(timeout):
        return None if timeout is None else monotonic() + max(0.0, float(timeout))

    @staticmethod
    def _remaining(deadline):
        return None if deadline is None else max(0.0, deadline - monotonic())

    def put(self, value, timeout=None):
        deadline = self._deadline(timeout)
        with self._mutex:
            while len(self._items) >= self._capacity:
                remaining = self._remaining(deadline)
                if remaining == 0.0:
                    raise TimeoutError("queue-full")
                self._contention_observed = True
                self._producer_waiting.set()
                self._not_full.wait(remaining)
            self._items.append(value)
            self._progress_count += 1
            self._not_full.notify()
            return None

    def take(self, timeout=None):
        deadline = self._deadline(timeout)
        with self._mutex:
            while not self._items:
                remaining = self._remaining(deadline)
                if remaining == 0.0:
                    raise TimeoutError("queue-empty")
                self._consumer_waiting.set()
                self._not_empty.wait(remaining)
            value = self._items.popleft()
            self._progress_count += 1
            self._not_empty.notify()
            return value

    def trace_snapshot(self):
        with self._mutex:
            return QueueTrace(
                progress_observed=self._progress_count > 0,
                resource_contention_observed=self._contention_observed,
                wait_condition_satisfiable=None,
            )
