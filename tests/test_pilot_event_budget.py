"""Focused tests for completed-turn token budget enforcement."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_cli_core.py"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("pilot_cli_core_budget", SCRIPT)
core = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(core)


def _usage(input_tokens: int, cached_input_tokens: int, output_tokens: int) -> dict:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_input_tokens": 0,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": 0,
    }


def _start_process(events: list[dict], *, delay: float = 0.0) -> subprocess.Popen[bytes]:
    lines = [json.dumps(event, separators=(",", ":")) for event in events]
    script = (
        "import json,sys,time\n"
        "for line in json.loads(sys.argv[1]): print(line, flush=True)\n"
        "time.sleep(float(sys.argv[2]))\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", script, json.dumps(lines), str(delay)],
        stdout=subprocess.PIPE,
    )


class EventBudgetTests(unittest.TestCase):
    def _collect(self, events: list[dict], *, max_tokens: int | None,
                 delay: float = 0.0):
        process = _start_process(events, delay=delay)
        lines, times, failure = core._collect_events(
            process, started=time.monotonic(), timeout=5,
            preserve_on_failure=True, max_tokens=max_tokens,
        )
        return process, lines, times, failure

    def test_budget_exceeded_on_valid_completed_turn_kills_and_preserves_events(self):
        event = {"type": "turn.completed", "usage": _usage(80, 20, 30)}
        process, lines, times, failure = self._collect(
            [event], max_tokens=100, delay=5,
        )
        self.assertEqual(failure, "token_budget_exceeded")
        self.assertEqual(lines, [json.dumps(event, separators=(",", ":"))])
        self.assertEqual(len(times), 1)
        self.assertIsNotNone(process.poll())

    def test_budget_is_cumulative_across_completed_turns(self):
        events = [
            {"type": "turn.completed", "usage": _usage(40, 10, 20)},
            {"type": "turn.completed", "usage": _usage(30, 5, 20)},
        ]
        process, lines, _times, failure = self._collect(
            events, max_tokens=100, delay=5,
        )
        self.assertEqual(failure, "token_budget_exceeded")
        self.assertEqual(len(lines), 2)
        self.assertIsNotNone(process.poll())

    def test_missing_or_malformed_usage_is_not_counted(self):
        events = [
            {"type": "turn.completed"},
            {"type": "turn.completed", "usage": _usage(10, 11, 10)},
            {"type": "turn.completed", "usage": {"input_tokens": 9000}},
        ]
        process, lines, _times, failure = self._collect(events, max_tokens=1)
        self.assertIsNone(failure)
        self.assertEqual(len(lines), 3)
        self.assertEqual(process.returncode, 0)

    def test_cached_input_is_already_in_input_total_and_not_counted_twice(self):
        event = {"type": "turn.completed", "usage": _usage(100, 90, 40)}
        process, lines, _times, failure = self._collect(
            [event], max_tokens=140, delay=0.05,
        )
        self.assertIsNone(failure)
        self.assertEqual(len(lines), 1)
        self.assertEqual(process.returncode, 0)

    def test_none_preserves_unbudgeted_collector_behavior(self):
        event = {"type": "turn.completed", "usage": _usage(1_000_000, 500_000, 1_000_000)}
        process, lines, _times, failure = self._collect([event], max_tokens=None)
        self.assertIsNone(failure)
        self.assertEqual(len(lines), 1)
        self.assertEqual(process.returncode, 0)

    def test_budget_must_be_positive_integer_within_reasonable_bound(self):
        class NoOutput:
            stdout = None

        for value in (True, False, 0, -1, 1.5, "100", core.MAX_EVENT_TOKEN_BUDGET + 1):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "max_tokens"):
                core._collect_events(
                    NoOutput(), started=time.monotonic(), timeout=1,
                    max_tokens=value,
                )


if __name__ == "__main__":
    unittest.main()
