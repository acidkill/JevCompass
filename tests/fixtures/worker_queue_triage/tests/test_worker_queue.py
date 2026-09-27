"""Frozen bounded queue contract checks; concurrency uses events, never sleeps."""
import queue
import threading
import unittest

from worker_queue import WorkerQueue


class WorkerQueueContractTests(unittest.TestCase):
    def test_preloaded_item_is_immediately_available_and_preserves_identity(self):
        work = WorkerQueue()
        token = object()
        work.put(token, timeout=0)
        self.assertIs(work.take(timeout=0), token)

    def test_falsey_values_are_valid_work_items(self):
        for value in (None, False, 0, ""):
            with self.subTest(value=type(value).__name__):
                work = WorkerQueue()
                work.put(value, timeout=0)
                self.assertIs(work.take(timeout=0), value)

    def test_fifo_order_and_exactly_once_consumption(self):
        work = WorkerQueue(capacity=2)
        first, second = object(), object()
        work.put(first, timeout=0)
        work.put(second, timeout=0)
        self.assertIs(work.take(timeout=0), first)
        self.assertIs(work.take(timeout=0), second)
        with self.assertRaises(TimeoutError):
            work.take(timeout=0)

    def test_full_queue_timeout_does_not_replace_existing_work(self):
        work = WorkerQueue()
        first, rejected = object(), object()
        work.put(first, timeout=0)
        with self.assertRaises(TimeoutError):
            work.put(rejected, timeout=0)
        self.assertIs(work.take(timeout=0), first)
        with self.assertRaises(TimeoutError):
            work.take(timeout=0)

    def test_empty_timeout_does_not_discard_later_work(self):
        work = WorkerQueue()
        with self.assertRaises(TimeoutError):
            work.take(timeout=0)
        token = object()
        work.put(token, timeout=0)
        self.assertIs(work.take(timeout=0), token)

    def test_publisher_wakes_waiting_consumer_promptly(self):
        work = WorkerQueue()
        result = queue.Queue()
        ready = threading.Event()
        token = object()

        def consume():
            try:
                result.put((True, work.take(timeout=0.8)))
            except Exception as exc:
                result.put((False, type(exc).__name__))
            finally:
                ready.set()

        worker = threading.Thread(target=consume, daemon=True)
        worker.start()
        try:
            self.assertTrue(work._consumer_waiting.wait(timeout=0.5))
            work.put(token, timeout=0)
            self.assertTrue(ready.wait(timeout=0.15), "consumer-was-not-woken")
            worker.join(timeout=0.15)
            self.assertFalse(worker.is_alive(), "consumer-did-not-finish")
            ok, value = result.get_nowait()
            self.assertTrue(ok, value)
            self.assertIs(value, token)
        finally:
            worker.join(timeout=1.0)

    def test_consumer_wakes_producer_waiting_for_capacity_promptly(self):
        work = WorkerQueue()
        result = queue.Queue()
        ready = threading.Event()
        first, second = object(), object()
        work.put(first, timeout=0)

        def produce():
            try:
                result.put((True, work.put(second, timeout=0.8)))
            except Exception as exc:
                result.put((False, type(exc).__name__))
            finally:
                ready.set()

        producer = threading.Thread(target=produce, daemon=True)
        producer.start()
        try:
            self.assertTrue(work._producer_waiting.wait(timeout=0.5))
            self.assertIs(work.take(timeout=0), first)
            self.assertTrue(ready.wait(timeout=0.15), "producer-was-not-woken")
            producer.join(timeout=0.15)
            self.assertFalse(producer.is_alive(), "producer-did-not-finish")
            ok, _ = result.get_nowait()
            self.assertTrue(ok)
            self.assertIs(work.take(timeout=0), second)
        finally:
            producer.join(timeout=1.0)

    def test_trace_shows_progress_and_contention_but_leaves_wait_fact_unknown(self):
        work = WorkerQueue()
        work.put(object(), timeout=0)
        result = queue.Queue()
        ready = threading.Event()

        def produce():
            try:
                work.put(object(), timeout=0.8)
                result.put("enqueued")
            except TimeoutError:
                result.put("timed-out")
            finally:
                ready.set()

        producer = threading.Thread(target=produce, daemon=True)
        producer.start()
        try:
            self.assertTrue(work._producer_waiting.wait(timeout=0.5))
            snapshot = work.trace_snapshot()
            self.assertTrue(snapshot.progress_observed)
            self.assertTrue(snapshot.resource_contention_observed)
            self.assertIsNone(snapshot.wait_condition_satisfiable)
            self.assertIsNotNone(work.take(timeout=0))
            self.assertTrue(ready.wait(timeout=0.15), "producer-was-not-woken")
            producer.join(timeout=0.15)
            self.assertFalse(producer.is_alive(), "producer-did-not-finish")
            self.assertEqual(result.get_nowait(), "enqueued")
            self.assertIsNotNone(work.take(timeout=0))
        finally:
            producer.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
