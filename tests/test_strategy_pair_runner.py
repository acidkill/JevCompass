"""Offline strategy command accounting checks."""
import importlib.util
import json
from pathlib import Path
import unittest
import shutil
import tempfile
from unittest.mock import patch
from tests.test_pilot_test_order_pair import command_event

SPEC = importlib.util.spec_from_file_location(
    "strategy_pair", Path(__file__).parents[1] / "scripts" / "pilot_strategy_pair.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class StrategyPairTests(unittest.TestCase):
    def test_choice_and_usage_are_captured_without_raw_output(self):
        payload = json.dumps({
            "status": "remote-choice",
            "strategies": [{"id": runner.STRATEGY_IDS[0], "rationale": "private-text"}],
            "usage": {"input_tokens": 30, "output_tokens": 4, "cost_usd": .001},
        })
        events = [
            command_event("item.started", "strategy", runner.STRATEGY_COMMAND),
            command_event("item.completed", "strategy", runner.STRATEGY_COMMAND,
                          exit_code=0, aggregated_output=payload),
        ]
        receipt = runner._event_receipts(events, [1, 2], 0)
        self.assertEqual(receipt["strategy"]["candidate_ids"], [runner.STRATEGY_IDS[0]])
        self.assertEqual(receipt["strategy_usage"]["input_tokens"], 30)
        self.assertEqual(receipt["strategy_latency_ms"], 1000)
        self.assertNotIn("private-text", json.dumps(receipt))

    def test_no_call_is_unknown_not_zero_cost(self):
        receipt = runner._event_receipts([], [], 0)
        self.assertEqual(receipt["strategy_usage"]["status"], "not_invoked")
        self.assertIsNone(receipt["strategy_usage"]["jev_provider_cost_usd"])

    def test_independent_validation_rejects_false_agent_success(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture"
            shutil.copytree(runner.engine.FIXTURE, fixture)
            with patch.object(runner, "_original_arm", return_value={"cli_status": "completed"}):
                receipt = runner._run_arm(fixture=fixture)
            self.assertEqual(receipt["cli_status"], "failed")
            self.assertTrue(receipt["immutable_files_preserved"])
            self.assertNotEqual(receipt["independent_validation"]["required"], 0)

    def test_correct_repair_passes_independent_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture"
            shutil.copytree(runner.engine.FIXTURE, fixture)
            def repair(**kwargs):
                path = fixture / runner.engine.CHANGED_FILE
                path.write_text(path.read_text().replace(
                    "weight_grams // 1000", "(weight_grams + 999) // 1000"))
                return {"cli_status": "completed"}
            with patch.object(runner, "_original_arm", side_effect=repair):
                receipt = runner._run_arm(fixture=fixture)
            self.assertEqual(receipt["independent_quality_status"], "frozen_checks_pass")
            self.assertEqual(receipt["independent_validation"],
                             {"unit": 0, "contract": 0, "required": 0})

    def test_mutated_tests_are_not_executed_as_trusted_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture"
            shutil.copytree(runner.engine.FIXTURE, fixture)
            def mutate(**kwargs):
                (fixture / "tests" / "tampered.py").write_text("private-source")
                return {"cli_status": "completed"}
            with patch.object(runner, "_original_arm", side_effect=mutate), \
                    patch.object(runner.subprocess, "run") as execute:
                receipt = runner._run_arm(fixture=fixture)
            execute.assert_not_called()
            self.assertFalse(receipt["immutable_files_preserved"])
            self.assertEqual(receipt["cli_status"], "failed")
            self.assertNotIn("private-source", json.dumps(receipt))
