"""Independent disposable-copy oracle for the worker-queue triage fixture."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "worker_queue_triage"
FOCUSED = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_worker_queue.py", "-v"]
FULL = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]


def _replace_once(source, before, after):
    if source.count(before) != 1:
        raise AssertionError("reference-anchor-changed")
    return source.replace(before, after, 1)


def reference_source():
    source = (FIXTURE / "worker_queue.py").read_text(encoding="utf-8")
    source = _replace_once(
        source,
        "            self._items.append(value)\n"
        "            self._progress_count += 1\n"
        "            self._not_full.notify()",
        "            self._items.append(value)\n"
        "            self._progress_count += 1\n"
        "            self._not_empty.notify()",
    )
    return _replace_once(
        source,
        "            value = self._items.popleft()\n"
        "            self._progress_count += 1\n"
        "            self._not_empty.notify()",
        "            value = self._items.popleft()\n"
        "            self._progress_count += 1\n"
        "            self._not_full.notify()",
    )


def run_copy(source, command):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "fixture"
        shutil.copytree(FIXTURE, root)
        (root / "worker_queue.py").write_text(source, encoding="utf-8")
        return subprocess.run(
            command, cwd=root, capture_output=True, text=True, timeout=4,
        )


class WorkerQueueTriageFixtureTests(unittest.TestCase):
    def test_starter_fails_focused_and_required_full_suites(self):
        starter = (FIXTURE / "worker_queue.py").read_text(encoding="utf-8")
        for command in (FOCUSED, FULL):
            with self.subTest(command=command[4:]):
                result = run_copy(starter, command)
                self.assertEqual(result.returncode, 1)
                self.assertIn("consumer-was-not-woken", result.stderr)
                self.assertIn("producer-was-not-woken", result.stderr)

    def test_reference_notification_repair_passes_focused_and_required_full(self):
        source = reference_source()
        for command in (FOCUSED, FULL):
            with self.subTest(command=command[4:]):
                result = run_copy(source, command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Ran 8 tests", result.stderr)

    def test_wrong_consumer_notification_mutant_is_rejected(self):
        source = reference_source()
        mutant = _replace_once(
            source,
            "            self._items.append(value)\n"
            "            self._progress_count += 1\n"
            "            self._not_empty.notify()",
            "            self._items.append(value)\n"
            "            self._progress_count += 1\n"
            "            self._not_full.notify()",
        )
        result = run_copy(mutant, FOCUSED)
        self.assertEqual(result.returncode, 1)
        self.assertIn("consumer-was-not-woken", result.stderr)

    def test_wrong_producer_notification_mutant_is_rejected(self):
        source = reference_source()
        mutant = _replace_once(
            source,
            "            value = self._items.popleft()\n"
            "            self._progress_count += 1\n"
            "            self._not_full.notify()",
            "            value = self._items.popleft()\n"
            "            self._progress_count += 1\n"
            "            self._not_empty.notify()",
        )
        result = run_copy(mutant, FOCUSED)
        self.assertEqual(result.returncode, 1)
        self.assertIn("producer-was-not-woken", result.stderr)

    def test_reverse_fifo_mutant_is_rejected(self):
        source = reference_source()
        mutant = _replace_once(source, "self._items.popleft()", "self._items.pop()")
        result = run_copy(mutant, FOCUSED)
        self.assertEqual(result.returncode, 1)
        self.assertIn("test_fifo_order_and_exactly_once_consumption", result.stderr)

    def test_no_consumption_mutant_is_rejected(self):
        source = reference_source()
        mutant = _replace_once(source, "self._items.popleft()", "self._items[0]")
        result = run_copy(mutant, FOCUSED)
        self.assertEqual(result.returncode, 1)
        self.assertIn("test_fifo_order_and_exactly_once_consumption", result.stderr)


if __name__ == "__main__":
    unittest.main()
