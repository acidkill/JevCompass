"""CLI output reports typed decision-cache hits without inventing provider usage."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from jevcompass.cli import main
from jevcompass.strategy import StrategyId, StrategyRecommendation, StrategyResult
from jevcompass.test_order import (
    Coverage,
    DecisionReason,
    RequiredTest,
    RuntimeBucket,
    TestCandidate,
    TestKind,
    TestOrderResult,
)
from jevcompass.triage import (
    DiagnosticStep,
    HypothesisId,
    TriageDecisionReason,
    TriageResult,
)


class TypedCacheCliTests(unittest.TestCase):
    def test_strategy_json_reports_cache_hit_and_defaults_false_for_older_result(self):
        cached = StrategyResult(
            recommendations=(StrategyRecommendation(StrategyId.REPRODUCE_FAILURE, "reproduce"),),
            status="remote-choice",
            cache_hit=True,
        )
        output = io.StringIO()
        with mock.patch("jevcompass.strategy.choose_strategies", return_value=cached), \
             contextlib.redirect_stdout(output):
            self.assertEqual(main(["strategy", "choose", "--kind", "coding", "--signal",
                                   "existing_symbol", "--json"]), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["cache_hit"])
        self.assertIsNone(result["usage"])

        legacy = SimpleNamespace(
            recommendations=cached.recommendations, status="no-remote-choice", usage=None
        )
        output = io.StringIO()
        with mock.patch("jevcompass.strategy.choose_strategies", return_value=legacy), \
             contextlib.redirect_stdout(output):
            self.assertEqual(main(["strategy", "choose", "--kind", "coding", "--signal",
                                   "existing_symbol", "--json"]), 0)
        result = json.loads(output.getvalue())
        self.assertFalse(result["cache_hit"])
        self.assertIsNone(result["usage"])

    def test_test_order_json_reports_cache_hit_and_defaults_false_for_older_result(self):
        candidate = TestCandidate(
            TestKind.UNIT, "python -m unittest tests.test_one", "unit", 0.5,
            Coverage.DIRECT, RuntimeBucket.FAST,
        )
        required = RequiredTest("python -m unittest discover -s tests", "full")
        cached = TestOrderResult(
            ordered_candidates=(candidate,), required=(required,),
            status="remote-choice", decision_reason=DecisionReason.ACCEPTED, cache_hit=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_text(json.dumps({
                "surface": "api",
                "candidates": [{"id": "unit", "kind": "unit",
                                "command": "python -m unittest tests.test_one", "relevance": 0.5}],
                "required": [{"id": "full", "command": "python -m unittest discover -s tests"}],
            }))
            output = io.StringIO()
            with mock.patch("jevcompass.test_order.rank_tests", return_value=cached), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(main(["tests", "rank", "--input", str(source), "--json"]), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result["cache_hit"])
            self.assertIsNone(result["usage"])
            self.assertFalse(result["executed"])

            legacy = SimpleNamespace(
                ordered_candidates=(candidate,), required=(required,), status="no-remote-choice",
                decision_reason=DecisionReason.NO_CHOICE_NEEDED, usage=None,
            )
            output = io.StringIO()
            with mock.patch("jevcompass.test_order.rank_tests", return_value=legacy), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(main(["tests", "rank", "--input", str(source), "--json"]), 0)
            result = json.loads(output.getvalue())
            self.assertFalse(result["cache_hit"])
            self.assertIsNone(result["usage"])
            self.assertFalse(result["executed"])

    def test_triage_cache_hit_preserves_exit_status_and_marks_cached_preferred_action(self):
        steps = (
            DiagnosticStep(HypothesisId.IMPORT_PATH_CHANGED, "path changed", "inspect import"),
            DiagnosticStep(HypothesisId.IMPORT_MODULE_MISSING, "module missing", "check package"),
        )
        cached = TriageResult(
            observed_exit_status=7, steps=steps, status="remote-choice",
            decision_reason=TriageDecisionReason.ACCEPTED, cache_hit=True,
        )
        output = io.StringIO()
        with mock.patch("jevcompass.triage.triage_failure", return_value=cached), \
             contextlib.redirect_stdout(output):
            self.assertEqual(main([
                "triage", "--exit-code", "7", "--kind", "import",
                "--hypothesis", "import_module_missing", "--hypothesis", "import_path_changed",
            ]), 0)
        self.assertIn("Cached preferred next action", output.getvalue())
        self.assertIn("Unranked fallback", output.getvalue())

        output = io.StringIO()
        with mock.patch("jevcompass.triage.triage_failure", return_value=cached), \
             contextlib.redirect_stdout(output):
            self.assertEqual(main([
                "triage", "--exit-code", "7", "--kind", "import",
                "--hypothesis", "import_module_missing", "--hypothesis", "import_path_changed",
                "--json",
            ]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["observed_exit_status"], 7)
        self.assertTrue(result["test_failed"])
        self.assertTrue(result["cache_hit"])
        self.assertIsNone(result["decision_usage"])
        self.assertFalse(result["executed"])
        self.assertEqual(result["steps"][0]["selection_source"], "cached_preferred_next_step")
        self.assertEqual(result["steps"][1]["selection_source"], "unranked_local_fallback")

        legacy = SimpleNamespace(
            observed_exit_status=7, test_failed=True, steps=steps, status="remote-choice",
            decision_usage=None, decision_reason=TriageDecisionReason.ACCEPTED,
        )
        output = io.StringIO()
        with mock.patch("jevcompass.triage.triage_failure", return_value=legacy), \
             contextlib.redirect_stdout(output):
            self.assertEqual(main([
                "triage", "--exit-code", "7", "--kind", "import",
                "--hypothesis", "import_module_missing", "--hypothesis", "import_path_changed",
                "--json",
            ]), 0)
        result = json.loads(output.getvalue())
        self.assertFalse(result["cache_hit"])
        self.assertEqual(result["steps"][0]["selection_source"], "remote_preferred_next_step")
        self.assertIsNone(result["decision_usage"])
        self.assertEqual(result["observed_exit_status"], 7)


if __name__ == "__main__":
    unittest.main()
