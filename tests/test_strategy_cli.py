"""CLI strategy selection without raw prompt intake."""

import contextlib
import io
import json
import unittest
from unittest import mock

from jevcompass.cli import main


class StrategyCliTests(unittest.TestCase):
    def test_outputs_only_reviewed_strategy_and_provenance(self):
        output = io.StringIO()
        with mock.patch("jevcompass.strategy.DecisionsClient") as client, contextlib.redirect_stdout(output):
            client.return_value.decide.return_value = {
                "strategy": {"type": "choice", "choice": "define_contract_then_implement", "confidence": 0.9}
            }
            self.assertEqual(main(["strategy", "choose", "--kind", "coding",
                                   "--signal", "existing_symbol", "--signal", "behavior_change",
                                   "--json"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "remote-choice")
        self.assertEqual(result["strategies"][0]["id"], "define_contract_then_implement")
        self.assertLessEqual(len(result["strategies"]), 2)

    def test_invalid_signal_rejected_before_remote_request(self):
        with mock.patch("jevcompass.strategy.DecisionsClient") as client, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exit_info:
                main(["strategy", "choose", "--kind", "coding", "--signal", "client-secret"])
        self.assertEqual(exit_info.exception.code, 2)
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
