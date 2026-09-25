"""Synthetic tests for privacy-safe pilot CLI receipt parsers."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pilot_receipts import parse_choice_receipt, parse_codex_json_events


class PilotReceiptTests(unittest.TestCase):
    def test_codex_usage_elapsed_and_explicit_first_action(self):
        events = [
            {"type": "thread.started", "thread_id": "private-thread"},
            {"type": "item.started", "item": {
                "id": "item_1", "type": "command_execution", "command": "cat /home/private/source.py",
                "output": "PRIVATE OUTPUT",
            }},
            {"type": "item.completed", "item": {
                "id": "item_1", "type": "command_execution", "exit_code": 0,
                "aggregated_output": "PRIVATE OUTPUT",
            }},
            {"type": "turn.completed", "usage": {
                "input_tokens": 120, "cached_input_tokens": 25, "output_tokens": 19,
                "reasoning_output_tokens": 3,
            }, "prompt": "PRIVATE PROMPT", "api_key": "PRIVATE KEY"},
        ]
        receipt = parse_codex_json_events(
            [json.dumps(item) for item in events],
            started_at=10.0, ended_at=12.5, event_times=[10.0, 10.42, 10.9, 12.4],
            validated_useful_action_ids=["item_1"],
        )
        self.assertEqual(receipt["usage_status"], "available")
        self.assertEqual(receipt["token_usage"], {
            "input_tokens": 120, "cached_input_tokens": 25, "output_tokens": 19,
            "uncached_input_proxy": 95,
        })
        self.assertEqual(receipt["elapsed_ms"], 2500.0)
        self.assertEqual(receipt["first_tool_start"], {
            "type": "command_execution", "elapsed_ms": 420.0,
        })
        self.assertEqual(receipt["first_useful_action"], {
            "type": "command_execution", "elapsed_ms": 900.0,
        })
        serialized = json.dumps(receipt)
        for private in ("private-thread", "/home/private", "PRIVATE OUTPUT", "PRIVATE PROMPT", "PRIVATE KEY", "cat"):
            self.assertNotIn(private, serialized)
        self.assertIsNone(receipt["billing_estimate"])

    def test_tool_start_is_not_assumed_useful_without_validation(self):
        events = [
            {"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": "private"}},
            {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "exit_code": 1}},
        ]
        result = parse_codex_json_events((json.dumps(item) for item in events),
                                         started_at=1.0, ended_at=2.0, event_times=[1.2, 1.6],
                                         validated_useful_action_ids=["item_1"])
        self.assertIsNotNone(result["first_tool_start"])
        self.assertIsNone(result["first_useful_action"])

    def test_malformed_duplicate_and_partial_usage_are_unscored(self):
        valid = {"input_tokens": 5, "cached_input_tokens": 1, "output_tokens": 2}
        cases = [
            [{"type": "turn.completed"}],
            [{"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}],
            [{"type": "turn.completed", "usage": {**valid, "output_tokens": True}}],
            [{"type": "turn.completed", "usage": {**valid, "cached_input_tokens": 6}}],
            [{"type": "turn.completed", "usage": valid},
             {"type": "turn.completed", "usage": valid}],
            [{"type": "turn.completed", "usage": valid},
             {"type": "turn.completed", "usage": {"input_tokens": 4}}],
        ]
        for events in cases:
            with self.subTest(events=events):
                receipt = parse_codex_json_events(json.dumps(item) for item in events)
                self.assertEqual(receipt["usage_status"], "unscored")
                self.assertIsNone(receipt["token_usage"])

    def test_missing_timings_or_action_event_are_unscored(self):
        receipt = parse_codex_json_events([
            json.dumps({"type": "item.completed", "item": {
                "type": "command_execution", "command": "private",
            }}),
        ], started_at=2.0)
        self.assertIsNone(receipt["elapsed_ms"])
        self.assertIsNone(receipt["first_useful_action"])

    def test_choice_receipts_keep_only_status_and_validated_candidate_ids(self):
        cases = (
            ("strategy", "strategies", ["define_contract_then_implement"]),
            ("test_order", "ordered", ["unit", "contract"]),
            ("triage", "steps", ["import_path_changed"]),
        )
        for kind, field, ids in cases:
            with self.subTest(kind=kind):
                receipt = parse_choice_receipt(
                    json.dumps({
                        "status": "remote-choice",
                        **({"observed_exit_status": 1, "test_failed": True} if kind == "triage" else {}),
                        field: [{"id": item, "rationale": "PRIVATE RATIONALE",
                                 "command": "private command", "path": "/private/source"} for item in ids],
                        "source": "PRIVATE SOURCE",
                    }),
                    choice_type=kind, candidate_ids=ids,
                )
                expected = {"status": "remote-choice", "candidate_ids": ids}
                if kind == "triage":
                    expected["observed_exit_status"] = 1
                self.assertEqual(receipt, expected)
                self.assertNotIn("PRIVATE", json.dumps(receipt))
                self.assertNotIn("command", json.dumps(receipt))
                self.assertNotIn("/private", json.dumps(receipt))

    def test_triage_inconsistent_exit_status_is_unscored(self):
        for exit_status, failed in ((0, True), (1, False), (True, True), (256, True)):
            with self.subTest(exit_status=exit_status, failed=failed):
                result = parse_choice_receipt(
                    {"status": "remote-choice", "steps": [{"id": "inspect_failure"}],
                     "observed_exit_status": exit_status, "test_failed": failed},
                    choice_type="triage", candidate_ids=["inspect_failure"],
                )
                self.assertEqual(result, {"status": "unscored", "candidate_ids": []})

    def test_invalid_choice_or_candidate_is_unscored(self):
        candidates = ["unit", "contract"]
        malformed = (
            ("not json", "test_order"),
            ({"status": "unknown", "ordered": [{"id": "unit"}]}, "test_order"),
            ({"status": "remote-choice", "ordered": [{"id": "external"}]}, "test_order"),
            ({"status": "remote-choice", "ordered": [{"id": "unit"}, {"id": "unit"}]}, "test_order"),
            ({"status": "remote-choice", "ordered": []}, "test_order"),
            ({"status": "remote-choice", "steps": [{"id": "unit"}]}, "strategy"),
        )
        for value, kind in malformed:
            with self.subTest(value=value, kind=kind):
                result = parse_choice_receipt(value, choice_type=kind, candidate_ids=candidates)
                self.assertEqual(result, {"status": "unscored", "candidate_ids": []})


if __name__ == "__main__":
    unittest.main()
