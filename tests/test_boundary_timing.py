"""Useful timing requires matched completed execution evidence."""
import unittest
from tests.test_boundary_pair_runner import runner
from tests.test_pilot_test_order_pair import command_event


class BoundaryTimingTests(unittest.TestCase):
    def receipt(self, code, output, times=None):
        command = runner.engine.CONTRACT_COMMAND
        events = [
            command_event("item.started", "contract", command),
            command_event("item.completed", "contract", command,
                          exit_code=code, aggregated_output=output),
        ]
        return runner._event_receipts(events, [1, 2] if times is None else times, 0)

    def test_success_and_relevant_failure(self):
        result = self.receipt(0, "test_money_representation_json_contract ... ok\nOK\n")
        self.assertEqual(result["first_successful_relevant_check_ms"], 2000)
        self.assertIsNone(result["first_useful_error_ms"])
        result = self.receipt(1, "FAIL: test_money_representation_json_contract (suite)\n")
        self.assertEqual(result["first_useful_error_ms"], 2000)
        self.assertEqual(result["first_useful_error_status"], "observed")

    def test_complete_frozen_suite_summary_without_names(self):
        result = self.receipt(0, "Ran 2 tests in 0.2s\n\nOK\n")
        self.assertEqual(result["first_successful_relevant_check_ms"], 2000)

    def test_unrelated_or_partial_output_and_missing_time_are_unscored(self):
        for code, output, times in [
            (0, "OK", [1, 2]),
            (0, "test_money_representation_json_contract ...", [1, 2]),
            (1, "FAIL: other_case", [1, 2]),
            (0, "test_money_representation_json_contract\nOK\n", [1]),
        ]:
            result = self.receipt(code, output, times)
            self.assertIsNone(result["first_successful_relevant_check_ms"])
            self.assertIsNone(result["first_useful_error_ms"])
