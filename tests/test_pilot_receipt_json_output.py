"""Tests for strict parsing of decision command JSON output."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pilot_receipts import parse_decision_command_output


class DecisionCommandOutputTests(unittest.TestCase):
    def test_parses_compact_and_pretty_printed_objects(self):
        expected = {"status": "no-remote-choice", "strategies": []}
        self.assertEqual(parse_decision_command_output(
            '{"status":"no-remote-choice","strategies":[]}'
        ), expected)
        self.assertEqual(parse_decision_command_output(
            '{\n  "status": "no-remote-choice",\n  "strategies": []\n}\n'
        ), expected)

    def test_accepts_noisy_output_only_when_complete_object_is_the_final_suffix(self):
        expected = {"status": "remote-choice", "strategies": [{"id": "trace_data_flow"}]}
        self.assertEqual(parse_decision_command_output(
            'starting local command...\nwarning: informational text\n'
            '{"status":"remote-choice","strategies":[{"id":"trace_data_flow"}]}\n'
        ), expected)
        self.assertIsNone(parse_decision_command_output(
            '{"status":"no-remote-choice"} trailing prose'
        ))

    def test_empty_malformed_null_and_array_inputs_return_none(self):
        for text in (
            "", "\n  ", "{", '{"x":}', '{"outer": {"good": 1}',
            'warning: {"outer": {"good": 1}', "null", "[]", '[{"x":1}]',
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_decision_command_output(text))

    def test_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        for text in (
            '{"status":"local","status":"remote"}',
            '{"outer":{"choice":"a","choice":"b"}}',
            '{"score":NaN}',
            '{"score":1e309}',
            '{"score":Infinity}',
            '{"score":-Infinity}',
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_decision_command_output(text))


if __name__ == "__main__":
    unittest.main()
