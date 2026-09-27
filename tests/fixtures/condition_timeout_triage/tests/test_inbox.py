"""Frozen inbox behavior checks using genuine bounded waits, without sleeps."""
import queue
import threading
import unittest

from inbox import Inbox


class InboxContractTests(unittest.TestCase):
    def test_preloaded_item_is_available_without_waiting(self):
        inbox = Inbox()
        token = object()
        inbox.publish(token)
        self.assertIs(inbox.take(timeout=0), token)

    def test_false_values_are_valid_messages(self):
        for value in (None, False, 0, ""):
            with self.subTest(value=value):
                inbox = Inbox()
                inbox.publish(value)
                self.assertIs(inbox.take(timeout=0), value)

    def test_fifo_and_exactly_one_consumption(self):
        inbox = Inbox()
        first, second = object(), object()
        inbox.publish(first)
        inbox.publish(second)
        self.assertIs(inbox.take(timeout=0), first)
        self.assertIs(inbox.take(timeout=0), second)
        with self.assertRaises(TimeoutError):
            inbox.take(timeout=0)

    def test_timeout_does_not_discard_later_publication(self):
        inbox = Inbox()
        with self.assertRaises(TimeoutError):
            inbox.take(timeout=0)
        token = object()
        inbox.publish(token)
        self.assertIs(inbox.take(timeout=0), token)

    def test_concurrent_publication_completes_consumer(self):
        inbox = Inbox()
        started = threading.Event()
        outcome = queue.Queue()
        token = object()
        def consume():
            started.set()
            try:
                outcome.put((True, inbox.take(timeout=0.25)))
            except Exception as exc:
                outcome.put((False, type(exc).__name__))
        worker = threading.Thread(target=consume, daemon=True)
        worker.start()
        try:
            self.assertTrue(started.wait(timeout=0.5))
            inbox.publish(token)
            worker.join(timeout=0.5)
            self.assertFalse(worker.is_alive())
            ok, value = outcome.get_nowait()
            self.assertTrue(ok)
            self.assertIs(value, token)
        finally:
            worker.join(timeout=0.5)


if __name__ == "__main__":
    unittest.main()
