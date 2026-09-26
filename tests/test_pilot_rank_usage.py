"""Offline aggregation and duplicate-event accounting checks."""
import json
import unittest
from tests.test_pilot_test_order_pair import runner, command_event


class RankUsageTests(unittest.TestCase):
    def payload(self, cost=.01):
        return json.dumps({"usage": {"input_tokens": 10, "output_tokens": 3,
                                     "cost_usd": cost}, "private": "ignored"})

    def test_complete_multiple_calls_and_unknown_cost(self):
        receipt = runner._rank_usage([(1, self.payload()), (2, self.payload())], 2)
        self.assertEqual(receipt["input_tokens"], 20)
        self.assertEqual(receipt["jev_provider_cost_usd"], .02)
        receipt = runner._rank_usage([(1, self.payload(None))], 1)
        self.assertEqual(receipt["output_tokens"], 3)
        self.assertIsNone(receipt["jev_provider_cost_usd"])

    def test_missing_invalid_duplicate_json_and_no_call(self):
        for payload in ['{"usage":{"input_tokens":true,"output_tokens":1}}',
                        '{"usage":{},"usage":{}}', "broken", self.payload(float("nan"))]:
            receipt = runner._rank_usage([(1, payload)], 1)
            self.assertEqual(receipt["status"], "incomplete")
            self.assertIsNone(receipt["input_tokens"])
        self.assertEqual(runner._rank_usage([], 0)["status"], "not_invoked")
        self.assertIsNone(runner._rank_usage([(1, self.payload())], 2)["input_tokens"])

    def test_duplicate_start_and_completion_count_once(self):
        command = "python -m jevcompass tests rank --input test-options.json --json"
        start = command_event("item.started", "rank", command)
        end = command_event("item.completed", "rank", command,
                            exit_code=0, aggregated_output=self.payload())
        receipt = runner._event_receipts([start, start, end, end], [1, 2, 3, 4], 0)
        self.assertEqual(receipt["rank_usage"]["invocation_count"], 1)
        self.assertEqual(receipt["rank_usage"]["input_tokens"], 10)
        self.assertNotIn("private", json.dumps(receipt))
