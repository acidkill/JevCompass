"""Strategy accounting survives abstention without altering local decisions."""
import json
import unittest
from jevcompass.decisions import DecisionsClient
from jevcompass.strategy import choose_strategies


class StrategyUsageTests(unittest.TestCase):
    def test_choice_and_uncertainty_retain_usage(self):
        for confidence, status in [(.9, "remote-choice"), (.1, "no-remote-choice")]:
            client = DecisionsClient(api_key="synthetic", transport=lambda *args: json.dumps({
                "answers": {"strategy": {"type": "choice", "choice": "trace_data_flow",
                                         "confidence": confidence}},
                "usage": {"input_tokens": 20, "output_tokens": 4},
            }).encode())
            result = choose_strategies("debugging", ["failing_test", "data_flow"], client=client)
            self.assertEqual(result.status, status)
            self.assertEqual(result.usage.input_tokens, 20)
            self.assertIsNone(result.usage.cost_usd)

    def test_local_singleton_never_requests_usage(self):
        calls = []
        client = DecisionsClient(api_key="synthetic", transport=lambda *args: calls.append(args))
        result = choose_strategies("debugging", ["failing_test"], client=client)
        self.assertIsNone(result.usage)
        self.assertEqual(calls, [])
