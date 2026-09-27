"""Tests for the reusable privacy-safe agent measurement receipt builder."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pilot_receipts import build_agent_measurement_receipt


def event_line(value):
    return json.dumps(value, separators=(",", ":"))


class AgentMeasurementReceiptTests(unittest.TestCase):
    def test_success_receipt_keeps_first_tool_start_usage_and_json_shape(self):
        lines = [
            event_line({"type": "item.started", "item": {
                "id": "cmd-1", "type": "command_execution",
                "command": "python -m unittest",
            }}),
            event_line({"type": "item.completed", "item": {
                "id": "cmd-1", "type": "command_execution", "exit_code": 0,
                "aggregated_output": "private test output",
            }}),
            event_line({"type": "turn.completed", "usage": {
                "input_tokens": 120, "cached_input_tokens": 40,
                "output_tokens": 12,
            }}),
        ]

        receipt = build_agent_measurement_receipt(
            lines, [1.1, 1.2, 1.3], 1.0, 1.5, 0, None,
        )

        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertFalse(receipt["collector_failed"])
        self.assertEqual(receipt["usage_status"], "available")
        self.assertEqual(receipt["token_usage"], {
            "input_tokens": 120,
            "cached_input_tokens": 40,
            "output_tokens": 12,
            "uncached_input_proxy": 80,
        })
        self.assertEqual(receipt["elapsed_ms"], 500.0)
        self.assertEqual(receipt["first_tool_start"], {
            "type": "command_execution", "elapsed_ms": 100.0,
        })
        self.assertNotIn("first_action", receipt)
        self.assertIsNone(receipt["billing_estimate"])
        encoded = json.dumps(receipt, allow_nan=False)
        self.assertEqual(json.loads(encoded), receipt)
        self.assertNotIn("private test output", encoded)
        self.assertNotIn("python -m unittest", encoded)

    def test_missing_usage_remains_unknown_not_zero(self):
        receipt = build_agent_measurement_receipt(
            [event_line({"type": "item.started", "item": {
                "id": "cmd-2", "type": "command_execution",
            }})],
            [4.25], 4.0, 4.5, 0, None,
        )

        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["usage_status"], "unscored")
        self.assertIsNone(receipt["token_usage"])
        self.assertIsNone(receipt["billing_estimate"])
        self.assertEqual(receipt["first_tool_start"], {
            "type": "command_execution", "elapsed_ms": 250.0,
        })
        json.dumps(receipt, allow_nan=False)

    def test_timeout_partial_events_and_nonzero_exit_stay_failed(self):
        receipt = build_agent_measurement_receipt(
            [event_line({"type": "item.started", "item": {
                "id": "cmd-3", "type": "command_execution",
                "command": "private command",
            }})],
            [7.05], 7.0, 8.0, -9, "timeout",
        )

        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["exit_code"], -9)
        self.assertTrue(receipt["collector_failed"])
        self.assertEqual(receipt["first_tool_start"], {
            "type": "command_execution", "elapsed_ms": 50.0,
        })
        self.assertEqual(receipt["usage_status"], "unscored")
        self.assertIsNone(receipt["token_usage"])
        encoded = json.dumps(receipt, allow_nan=False)
        self.assertNotIn("timeout", encoded)
        self.assertNotIn("private command", encoded)

    def test_collector_failure_overrides_zero_exit_and_partial_receipt_serializes(self):
        receipt = build_agent_measurement_receipt(
            [], [], 2.0, 2.2, 0, "event-reader-error",
        )

        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertTrue(receipt["collector_failed"])
        self.assertIsNone(receipt["first_tool_start"])
        self.assertEqual(receipt["usage_status"], "unscored")
        self.assertIsNone(receipt["token_usage"])
        encoded = json.dumps(receipt, allow_nan=False)
        self.assertEqual(json.loads(encoded), receipt)
        self.assertNotIn("event-reader-error", encoded)

    def test_empty_non_none_collector_failure_marker_is_still_failure(self):
        for marker in ("", 0, False):
            with self.subTest(marker=marker):
                receipt = build_agent_measurement_receipt(
                    [], [], 1.0, 1.1, 0, marker,
                )
                self.assertEqual(receipt["status"], "failed")
                self.assertTrue(receipt["collector_failed"])


if __name__ == "__main__":
    unittest.main()
