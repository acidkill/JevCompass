"""Equal-runtime local decisions preserve explicit ambiguities and required checks."""
import unittest
from unittest.mock import patch
from jevcompass.test_order import rank_tests, DecisionReason

class Client:
    def __init__(self):
        self.calls = 0
    def decide(self, state, questions):
        self.calls += 1
        return {"first": {"type": "choice", "choice": "t1", "confidence": .9}}

def candidates(runtime):
    return [
        {"id": "indirect", "kind": "integration", "command": "full", "coverage": "indirect", "runtime": runtime},
        {"id": "direct", "kind": "unit", "command": "focused", "coverage": "direct", "runtime": runtime},
    ]

class EqualRuntimeTests(unittest.TestCase):
    def test_known_equal_runtime_skips_client_and_preserves_required(self):
        required = [{"id": "full", "command": "full"}, {"id": "focused", "command": "focused"}]
        for runtime in ("fast", "slow"):
            with self.subTest(runtime=runtime), patch("jevcompass.test_order.DecisionsClient", side_effect=AssertionError("unexpected remote")):
                result = rank_tests("python", candidates(runtime), required)
                self.assertEqual(result.ordered_ids, ("direct", "indirect"))
                self.assertEqual(result.decision_reason, DecisionReason.LOCAL_RESOLUTION)
                self.assertEqual([x.command for x in result.required], ["full", "focused"])
                self.assertIsNone(result.usage)
                self.assertFalse(result.cache_hit)

    def test_ambiguities_still_reach_client(self):
        for case in ("unknown", "mixed", "signals", "targets", "multiple_direct"):
            with self.subTest(case=case):
                rows = candidates("fast")
                signals = []
                if case == "unknown":
                    rows = candidates("unknown")
                elif case == "mixed":
                    rows[1]["runtime"] = "slow"
                elif case == "signals":
                    signals = ["internal_logic_changed"]
                elif case == "targets":
                    rows[0]["coverage_targets"] = ["public_contract_changed"]
                else:
                    rows[0]["coverage"] = "direct"
                client = Client()
                rank_tests("python", rows, [], client, signals=signals)
                self.assertEqual(client.calls, 1)
