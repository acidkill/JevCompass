"""Offline tests for caller-resolved local strategy selection."""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from jevcompass.cli import main
from jevcompass.strategy import StrategyId, choose_strategies


class ResolvedStrategyTests(unittest.TestCase):
    def test_eligible_resolved_strategy_returns_only_reviewed_local_recommendation(self):
        client = Mock()
        with patch("jevcompass.strategy.DecisionsClient") as client_type:
            result = choose_strategies(
                "debugging",
                ["failing_test", "data_flow"],
                client=client,
                resolved_strategy=StrategyId.TRACE_DATA_FLOW,
            )

        self.assertEqual([item.id for item in result.recommendations], [StrategyId.TRACE_DATA_FLOW])
        self.assertIn("Trace the affected data flow", result.recommendations[0].rationale)
        self.assertEqual(result.status, "no-remote-choice")
        self.assertIsNone(result.usage)
        client.decide.assert_not_called()
        client_type.assert_not_called()

    def test_ineligible_known_strategy_fails_closed_without_remote_call(self):
        client = Mock()
        with patch("jevcompass.strategy.DecisionsClient") as client_type:
            result = choose_strategies(
                "debugging", ["failing_test"], client=client,
                resolved_strategy="define_contract_then_implement",
            )

        self.assertEqual(result.recommendations, ())
        self.assertEqual(result.status, "no-remote-choice")
        self.assertIsNone(result.usage)
        client.decide.assert_not_called()
        client_type.assert_not_called()

    def test_invalid_resolved_strategy_fails_closed_without_remote_call(self):
        client = Mock()
        with patch("jevcompass.strategy.DecisionsClient") as client_type:
            result = choose_strategies(
                "debugging", ["failing_test", "data_flow"], client=client,
                resolved_strategy="unreviewed-strategy",
            )

        self.assertEqual(result.recommendations, ())
        self.assertEqual(result.status, "no-remote-choice")
        self.assertIsNone(result.usage)
        client.decide.assert_not_called()
        client_type.assert_not_called()

    def test_none_preserves_ambiguous_remote_choice_path(self):
        with patch("jevcompass.strategy.DecisionsClient") as client_type:
            client_type.return_value.decide.return_value = {
                "strategy": {
                    "type": "choice",
                    "choice": StrategyId.TRACE_DATA_FLOW.value,
                    "confidence": 0.9,
                }
            }
            result = choose_strategies("debugging", ["failing_test", "data_flow"])

        client_type.assert_called_once_with()
        client_type.return_value.decide.assert_called_once()
        self.assertEqual(result.recommendations[0].id, StrategyId.TRACE_DATA_FLOW)
        self.assertEqual(result.status, "remote-choice")

    def test_cli_resolved_option_returns_local_result_without_client(self):
        output = io.StringIO()
        with patch("jevcompass.strategy.DecisionsClient") as client_type, contextlib.redirect_stdout(output):
            code = main([
                "strategy", "choose", "--kind", "debugging",
                "--signal", "failing_test", "--signal", "data_flow",
                "--resolved-strategy", "trace_data_flow", "--json",
            ])

        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "no-remote-choice")
        self.assertEqual([item["id"] for item in payload["strategies"]], ["trace_data_flow"])
        self.assertIsNone(payload["usage"])
        client_type.assert_not_called()

    def test_cli_rejects_unknown_resolved_choice(self):
        with patch("jevcompass.strategy.DecisionsClient") as client_type, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main([
                    "strategy", "choose", "--kind", "debugging",
                    "--signal", "failing_test", "--resolved-strategy", "unreviewed",
                ])

        self.assertEqual(raised.exception.code, 2)
        client_type.assert_not_called()


if __name__ == "__main__":
    unittest.main()
