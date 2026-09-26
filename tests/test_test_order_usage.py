"""Provider accounting must survive unusable test-order decisions."""
import io
import json
import unittest
from contextlib import redirect_stdout

from jevcompass.cli import _display_test_order
from jevcompass.decisions import DecisionsClient
from jevcompass.test_order import rank_tests


class TestOrderUsageTests(unittest.TestCase):
    def rank(self, answer, usage):
        client = DecisionsClient(api_key="synthetic", transport=lambda *args: json.dumps({
            "answers": {"first": answer}, "usage": usage,
        }).encode())
        return rank_tests("api", [
            {"kind": "unit", "command": "private-unit", "id": "local-unit"},
            {"kind": "contract", "command": "private-contract", "id": "local-contract"},
        ], [{"command": "required", "id": "full"}], client)

    def test_usage_retained_for_choice_and_abstention(self):
        for choice, confidence, status in [
            ("t2", .9, "remote-choice"), ("t1", .1, "no-remote-choice"),
            ("unknown", .9, "no-remote-choice"),
        ]:
            with self.subTest(choice=choice, confidence=confidence):
                result = self.rank({"type": "choice", "choice": choice,
                                    "confidence": confidence},
                                   {"input_tokens": 40, "output_tokens": 7, "cost": .002})
                self.assertEqual(result.status, status)
                self.assertEqual(result.usage.input_tokens, 40)
                self.assertEqual(result.usage.cost_usd, .002)
                self.assertEqual(result.required[0].command, "required")

    def test_unknown_cost_and_invalid_usage(self):
        answer = {"type": "choice", "choice": "t1", "confidence": .9}
        result = self.rank(answer, {"input_tokens": 40, "output_tokens": 7})
        self.assertIsNone(result.usage.cost_usd)
        self.assertIsNone(self.rank(answer, {"input_tokens": True, "output_tokens": 7}).usage)

    def test_local_skip_never_calls_transport(self):
        client = DecisionsClient(api_key="synthetic",
                                 transport=lambda *args: self.fail("local skip called API"))
        result = rank_tests("api", [{"kind": "unit", "command": "local"}], [], client)
        self.assertIsNone(result.usage)

    def test_cli_numeric_receipt_with_unknown_cost(self):
        result = self.rank({"type": "choice", "choice": "t1", "confidence": .1},
                           {"input_tokens": 40, "output_tokens": 7})
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(_display_test_order(result, True), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["usage"], {
            "input_tokens": 40, "output_tokens": 7, "cost_usd": None})
        self.assertFalse(payload["executed"])
