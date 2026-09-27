"""Offline strategy command accounting checks."""
import importlib.util
import json
from pathlib import Path
import unittest
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
