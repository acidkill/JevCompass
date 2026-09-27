"""Verify the prospective timeout fixture independently in disposable copies."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "condition_timeout_triage"


def run_copy(source=None):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "fixture"
        shutil.copytree(FIXTURE, root)
        if source is not None:
            (root / "inbox.py").write_text(source, encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=root, capture_output=True, text=True, timeout=3,
        )


def repaired_source():
    original = (FIXTURE / "inbox.py").read_text(encoding="utf-8")
    old = "lambda: self._ready"
    if original.count(old) != 1:
        raise AssertionError("predicate-anchor-changed")
    return original.replace(old, "lambda: bool(self._items)", 1)


class ConditionTimeoutFixtureTests(unittest.TestCase):
    def test_stale_readiness_flag_fails_preloaded_message_contract(self):
        result = run_copy()
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: test_preloaded_item_is_available_without_waiting", result.stderr)

    def test_queue_predicate_repair_passes_frozen_full_suite(self):
        result = run_copy(repaired_source())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 5 tests", result.stderr)

    def test_payload_truthiness_mutant_is_rejected(self):
        source = repaired_source().replace(
            "lambda: bool(self._items)",
            "lambda: bool(self._items) and bool(self._items[0])", 1,
        )
        result = run_copy(source)
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: test_false_values_are_valid_messages", result.stderr)

    def test_latest_message_first_mutant_is_rejected(self):
        source = repaired_source().replace("self._items.popleft()", "self._items.pop()", 1)
        result = run_copy(source)
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL: test_fifo_and_exactly_one_consumption", result.stderr)

    def test_return_without_consumption_mutant_is_rejected(self):
        source = repaired_source().replace("self._items.popleft()", "self._items[0]", 1)
        result = run_copy(source)
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL: test_fifo_and_exactly_one_consumption", result.stderr)


if __name__ == "__main__":
    unittest.main()
