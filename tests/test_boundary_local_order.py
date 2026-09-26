"""Narrow evidence-driven local coverage choice."""
import unittest
from jevcompass.test_order import rank_tests


class BoundaryLocalOrderTests(unittest.TestCase):
    def candidates(self, runtime="unknown", coverage="indirect"):
        return [
            {"id": "unit", "kind": "unit", "command": "unit", "coverage": coverage,
             "runtime": runtime},
            {"id": "contract", "kind": "contract", "command": "contract",
             "coverage": "direct", "runtime": "unknown"},
        ]

    def test_confirmed_boundary_with_unknown_runtime_is_local(self):
        class Never:
            def decide(self, *args):
                raise AssertionError("must not call provider")
        client = Never()
        # Count explicitly: the production fallback also catches exceptions.
        client.calls = 0
        def fail(*args):
            client.calls += 1
            raise AssertionError("unexpected request")
        client.decide = fail
        result = rank_tests("api", self.candidates(), [{"command": "full"}], client,
                            signals=["boundary_mapping_changed"])
        self.assertEqual(result.ordered_ids, ("contract", "unit"))
        self.assertEqual(client.calls, 0)
        self.assertIsNone(result.usage)
        self.assertEqual(result.required[0].command, "full")

    def test_real_runtime_or_unknown_coverage_keeps_choice_eligible(self):
        class Client:
            calls = 0
            def decide(self, *args):
                self.calls += 1
                return {"first": {"type": "choice", "choice": "t1", "confidence": .9}}
        for candidates, signals in [
            (self.candidates(runtime="fast"), ["boundary_mapping_changed"]),
            (self.candidates(coverage="unknown"), ["boundary_mapping_changed"]),
            (self.candidates(), []),
        ]:
            client = Client()
            result = rank_tests("api", candidates, [], client, signals=signals)
            self.assertEqual(client.calls, 1)
            self.assertEqual(result.status, "remote-choice")
