"""Focused offline tests for the pretask strategy selector."""
from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from jevcompass.decisions import DecisionsError
from jevcompass.strategy import (
    StrategyId,
    TaskKind,
    TaskSignal,
    choose_strategies,
)


class StrategySelectionTests(unittest.TestCase):
    def test_no_applicable_signal_returns_no_strategy_without_remote_call(self):
        client = Mock()
        self.assertEqual(choose_strategies(TaskKind.CODING, [], client=client).recommendations, ())
        client.decide.assert_not_called()

    def test_clear_local_choice_skips_remote_request(self):
        client = Mock()
        result = choose_strategies(
            TaskKind.DEBUGGING, [TaskSignal.FAILING_TEST], client=client,
        )
        self.assertEqual([item.id for item in result.recommendations], [StrategyId.REPRODUCE_FAILURE])
        self.assertEqual(result.status, "no-remote-choice")
        client.decide.assert_not_called()

    def test_single_signal_relevant_strategy_never_constructs_remote_client(self):
        with patch("jevcompass.strategy.DecisionsClient") as client_type:
            result = choose_strategies(
                TaskKind.DEBUGGING,
                [TaskSignal.FAILING_TEST],
            )
        client_type.assert_not_called()
        self.assertEqual(result.status, "no-remote-choice")
        self.assertEqual(
            [item.id for item in result.recommendations],
            [StrategyId.REPRODUCE_FAILURE],
        )

    def test_ambiguous_choice_uses_existing_decisions_client_by_default(self):
        with patch("jevcompass.strategy.DecisionsClient") as client_type:
            client_type.return_value.decide.return_value = {
                "strategy": {
                    "type": "choice",
                    "choice": StrategyId.TRACE_DATA_FLOW.value,
                    "confidence": 0.9,
                }
            }
            result = choose_strategies(
                TaskKind.DEBUGGING,
                [TaskSignal.FAILING_TEST, TaskSignal.DATA_FLOW],
            )
        client_type.assert_called_once_with()
        client_type.return_value.decide.assert_called_once()
        self.assertEqual(result.recommendations[0].id, StrategyId.TRACE_DATA_FLOW)
        self.assertEqual(result.status, "remote-choice")

    def test_remote_choice_reorders_only_local_candidates(self):
        client = Mock()
        client.decide.return_value = {
            "strategy": {
                "type": "choice",
                "choice": StrategyId.TRACE_DATA_FLOW.value,
                "confidence": 0.9,
            }
        }
        result = choose_strategies(
            TaskKind.DEBUGGING,
            [TaskSignal.FAILING_TEST, TaskSignal.DATA_FLOW],
            client=client,
        )
        self.assertEqual(result.recommendations[0].id, StrategyId.TRACE_DATA_FLOW)
        self.assertEqual(result.status, "remote-choice")
        self.assertLessEqual(len(result.recommendations), 2)
        state, questions = client.decide.call_args.args
        self.assertEqual(state, {
            "task_kind": "debugging",
            "signals": ["data_flow", "failing_test"],
        })
        self.assertEqual(set(questions["strategy"]["criteria"]), {
            StrategyId.REPRODUCE_FAILURE.value,
            StrategyId.TRACE_DATA_FLOW.value,
        })

    def test_private_prompt_code_and_paths_are_never_sent(self):
        client = Mock()
        client.decide.return_value = {
            "strategy": {
                "type": "choice",
                "choice": StrategyId.TRACE_DATA_FLOW.value,
                "confidence": 0.9,
            }
        }
        # The public input shape accepts only enums or their exact allowlisted values.
        choose_strategies(
            TaskKind.DEBUGGING,
            [TaskSignal.FAILING_TEST, TaskSignal.DATA_FLOW],
            client=client,
        )
        client.decide.assert_called_once()
        encoded = json.dumps(client.decide.call_args.args)
        for secret in (
            "private prompt text", "source-code-secret", "/private/project/file.py",
        ):
            self.assertNotIn(secret, encoded)
        self.assertEqual(
            choose_strategies("private prompt text", [], client=client).recommendations,
            (),
        )

    def test_malformed_unknown_or_uncertain_choices_fall_back_locally(self):
        fallback_client = Mock()
        fallback_client.decide.side_effect = DecisionsError("unavailable")
        fallback = choose_strategies(
            TaskKind.DEBUGGING,
            [TaskSignal.FAILING_TEST, TaskSignal.DATA_FLOW],
            client=fallback_client,
        )
        invalid_answers = (
            {"strategy": {"type": "free_text", "choice": "invented", "confidence": 0.99}},
            {"strategy": {"type": "choice", "choice": "invented", "confidence": 0.99}},
            {"strategy": {"type": "choice", "choice": StrategyId.TRACE_DATA_FLOW.value, "confidence": 0.4}},
            {"strategy": {"type": "choice", "choice": StrategyId.TRACE_DATA_FLOW.value, "confidence": True}},
            {"strategy": {"type": "choice", "choice": StrategyId.TRACE_DATA_FLOW.value, "confidence": 10 ** 10000}},
            {},
            [],
        )
        for answers in invalid_answers:
            with self.subTest(answers=answers):
                client = Mock()
                client.decide.return_value = answers
                result = choose_strategies(
                    TaskKind.DEBUGGING,
                    [TaskSignal.FAILING_TEST, TaskSignal.DATA_FLOW],
                    client=client,
                )
                self.assertEqual(result.recommendations, fallback.recommendations)
                self.assertEqual(result.status, "no-remote-choice")

    def test_remote_failure_and_timeout_use_deterministic_fallback(self):
        fallback_client = Mock()
        fallback_client.decide.side_effect = DecisionsError("unavailable")
        fallback = choose_strategies(
            TaskKind.DEBUGGING,
            [TaskSignal.FAILING_TEST, TaskSignal.DATA_FLOW],
            client=fallback_client,
        )
        for error in (DecisionsError("unavailable"), TimeoutError("slow")):
            client = Mock()
            client.decide.side_effect = error
            result = choose_strategies(
                TaskKind.DEBUGGING,
                [TaskSignal.FAILING_TEST, TaskSignal.DATA_FLOW],
                client=client,
            )
            self.assertEqual(result.recommendations, fallback.recommendations)
            self.assertEqual(result.status, "no-remote-choice")

    def test_invalid_task_kind_or_signal_fails_closed(self):
        client = Mock()
        self.assertEqual(choose_strategies("unknown", [], client=client).recommendations, ())
        self.assertEqual(
            choose_strategies(TaskKind.CODING, ["source-code-secret"], client=client).recommendations,
            (),
        )
        client.decide.assert_not_called()


if __name__ == "__main__":
    unittest.main()
