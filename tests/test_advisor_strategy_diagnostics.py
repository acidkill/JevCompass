"""Opt-in hook diagnostics time the complete strategy-enabled evaluation safely."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from jevcompass import advisor, strategy


PROMPT = (
    "Implement a Python change to the behavior of the existing function normalize: "
    "preserve output ordering. private-sentinel-xyz"
)


class StrategyDiagnosticTests(unittest.TestCase):
    def _run_hook(self, *, strategy_advice=True, chooser=None, clock=None):
        event = {"hook_event_name": "UserPromptSubmit", "prompt": PROMPT}
        stdin = SimpleNamespace(buffer=io.BytesIO(json.dumps(event).encode()))
        captured = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "advisor.jsonl"
            patches = [
                mock.patch.object(advisor, "LOG_PATH", log),
                mock.patch.dict(os.environ, {"JEV_ADVISOR_DIAGNOSTIC": "1"}),
                mock.patch.object(advisor.sys, "stdin", stdin),
                mock.patch.object(advisor, "select_advice", return_value=None),
                redirect_stdout(captured),
            ]
            if clock is not None:
                patches.append(mock.patch.object(advisor.time, "monotonic", side_effect=lambda: clock[0]))
            if chooser is not None:
                patches.append(mock.patch.object(strategy, "choose_strategies", side_effect=chooser))
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                if clock is not None and chooser is not None:
                    with patches[5], patches[6]:
                        advisor.hook_main(strategy_advice=strategy_advice)
                elif clock is not None:
                    with patches[5]:
                        advisor.hook_main(strategy_advice=strategy_advice)
                elif chooser is not None:
                    with patches[5]:
                        advisor.hook_main(strategy_advice=strategy_advice)
                else:
                    advisor.hook_main(strategy_advice=strategy_advice)
            rows = [json.loads(line) for line in log.read_text().splitlines()]
        return rows, captured.getvalue()

    def test_total_metric_includes_strategy_phase_and_is_allowlisted(self):
        from jevcompass.strategy import StrategyId, StrategyRecommendation, StrategyResult

        clock = [100.0]
        result = StrategyResult(
            (StrategyRecommendation(StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT, "private response text"),),
            "remote-choice", usage={"input_tokens": 9}, cache_hit=False,
        )

        def choose(*_args):
            clock[0] += 4.0
            return result

        rows, _ = self._run_hook(chooser=choose, clock=clock)
        total = [row for row in rows if row.get("status") == "strategy-hook-evaluation"]
        self.assertEqual(len(total), 1)
        row = total[0]
        self.assertEqual(row["category"], "coding")
        self.assertEqual(row["strategy_outcome"], "accepted")
        self.assertEqual(row["strategy_source"], "remote")
        self.assertGreaterEqual(row["duration_ms"], 4000)
        self.assertTrue(set(row) <= {
            "event", "category", "status", "profile", "duration_ms", "trace",
            "strategy_outcome", "strategy_source",
        })
        serialized = json.dumps(rows)
        self.assertNotIn("private-sentinel-xyz", serialized)
        self.assertNotIn("private response text", serialized)
        self.assertNotIn("input_tokens", serialized)

    def test_cached_choice_and_selector_error_have_fixed_provenance(self):
        from jevcompass.strategy import StrategyId, StrategyRecommendation, StrategyResult

        cached = StrategyResult(
            (StrategyRecommendation(StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT, "ignored"),),
            "remote-choice", usage=None, cache_hit=True,
        )
        rows, _ = self._run_hook(chooser=lambda *_: cached)
        record = next(row for row in rows if row.get("status") == "strategy-hook-evaluation")
        self.assertEqual((record["strategy_outcome"], record["strategy_source"]), ("accepted", "cached"))

        rows, _ = self._run_hook(chooser=RuntimeError("private error"))
        record = next(row for row in rows if row.get("status") == "strategy-hook-evaluation")
        self.assertEqual((record["strategy_outcome"], record["strategy_source"]), ("selector-error", "none"))
        self.assertNotIn("private error", json.dumps(rows))

    def test_default_off_keeps_existing_diagnostics_without_strategy_row(self):
        rows, _ = self._run_hook(strategy_advice=False)
        self.assertFalse(any(row.get("status") == "strategy-hook-evaluation" for row in rows))
        self.assertTrue(any(row.get("status", "").startswith("collab-") for row in rows))

    def test_empty_request_signals_are_recorded_without_strategy_call(self):
        event_prompt = "Implement this small update."
        event = {"hook_event_name": "UserPromptSubmit", "prompt": event_prompt}
        stdin = SimpleNamespace(buffer=io.BytesIO(json.dumps(event).encode()))
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "advisor.jsonl"
            with mock.patch.object(advisor, "LOG_PATH", log), \
                 mock.patch.dict(os.environ, {"JEV_ADVISOR_DIAGNOSTIC": "1"}), \
                 mock.patch.object(advisor.sys, "stdin", stdin), \
                 mock.patch.object(advisor, "select_advice", return_value=None), \
                 mock.patch.object(strategy, "choose_strategies") as choose, \
                 redirect_stdout(io.StringIO()):
                advisor.hook_main(strategy_advice=True)
            choose.assert_not_called()
            rows = [json.loads(line) for line in log.read_text().splitlines()]
        record = next(row for row in rows if row.get("status") == "strategy-hook-evaluation")
        self.assertEqual((record["strategy_outcome"], record["strategy_source"]), ("no-signals", "none"))


if __name__ == "__main__":
    unittest.main()
