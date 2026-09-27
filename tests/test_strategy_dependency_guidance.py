"""Dependency migration guidance is locally composed and strategy-scoped."""
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


GUIDANCE = (
    "Dependency migration checklist: verify the new API and signature; preserve "
    "the callers' public contract; validate exception propagation and resource ownership."
)
GENERIC_INSPECTION = "Inspect dependency behavior and existing symbol use before editing."


class DependencyGuidanceTests(unittest.TestCase):
    def test_local_inspection_gets_fixed_guidance_without_constructing_client(self):
        client = Mock()
        result = choose_strategies(
            TaskKind.CODING,
            [TaskSignal.DEPENDENCY_CHANGE],
            client=client,
        )

        self.assertEqual(result.status, "no-remote-choice")
        self.assertEqual(len(result.recommendations), 1)
        recommendation = result.recommendations[0]
        self.assertEqual(recommendation.id, StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE)
        self.assertEqual(recommendation.rationale, f"{GENERIC_INSPECTION} {GUIDANCE}")
        client.decide.assert_not_called()

    def test_explicit_local_resolution_gets_guidance_without_client_construction(self):
        with patch(
            "jevcompass.strategy.DecisionsClient",
            side_effect=AssertionError("client constructed"),
        ) as client_factory:
            result = choose_strategies(
                TaskKind.CODING,
                [TaskSignal.DEPENDENCY_CHANGE],
                resolved_strategy=StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE,
            )

        client_factory.assert_not_called()
        self.assertEqual(result.status, "no-remote-choice")
        self.assertEqual(
            result.recommendations[0].rationale,
            f"{GENERIC_INSPECTION} {GUIDANCE}",
        )

    def test_existing_symbol_inspection_without_dependency_signal_stays_generic(self):
        client = Mock()
        result = choose_strategies(
            TaskKind.CODING,
            [TaskSignal.EXISTING_SYMBOL],
            client=client,
        )

        self.assertEqual(
            result.recommendations[0].id,
            StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE,
        )
        self.assertEqual(result.recommendations[0].rationale, GENERIC_INSPECTION)
        self.assertNotIn("Dependency migration checklist", result.recommendations[0].rationale)
        client.decide.assert_not_called()

    def test_accepted_remote_inspection_gets_local_guidance_without_sending_it(self):
        client = Mock()
        client.decide.return_value = {
            "strategy": {
                "type": "choice",
                "choice": StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE.value,
                "confidence": 0.9,
            }
        }

        result = choose_strategies(
            TaskKind.CODING,
            [TaskSignal.DEPENDENCY_CHANGE, TaskSignal.BEHAVIOR_CHANGE],
            client=client,
        )

        self.assertEqual(result.status, "remote-choice")
        self.assertEqual(
            result.recommendations[0].id,
            StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE,
        )
        self.assertEqual(result.recommendations[0].rationale, f"{GENERIC_INSPECTION} {GUIDANCE}")
        client.decide.assert_called_once()
        state, questions = client.decide.call_args.args
        self.assertEqual(state, {
            "task_kind": "coding",
            "signals": ["behavior_change", "dependency_change"],
        })
        self.assertNotIn(GUIDANCE, json.dumps((state, questions)))

    def test_remote_fallback_inspection_gets_same_local_guidance(self):
        client = Mock()
        client.decide.side_effect = DecisionsError("offline")

        result = choose_strategies(
            TaskKind.CODING,
            [TaskSignal.DEPENDENCY_CHANGE, TaskSignal.BEHAVIOR_CHANGE],
            client=client,
        )

        self.assertEqual(result.status, "no-remote-choice")
        self.assertEqual(
            result.recommendations[0].id,
            StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE,
        )
        self.assertEqual(result.recommendations[0].rationale, f"{GENERIC_INSPECTION} {GUIDANCE}")
        client.decide.assert_called_once()

    def test_other_strategy_ids_do_not_receive_dependency_guidance(self):
        client = Mock()
        result = choose_strategies(
            TaskKind.REVIEW,
            [TaskSignal.DEPENDENCY_CHANGE, TaskSignal.DATA_FLOW],
            client=client,
            resolved_strategy=StrategyId.TRACE_DATA_FLOW,
        )

        self.assertEqual(result.status, "no-remote-choice")
        self.assertEqual(result.recommendations[0].id, StrategyId.TRACE_DATA_FLOW)
        self.assertEqual(
            result.recommendations[0].rationale,
            "Trace the affected data flow and identify the narrowest safe change.",
        )
        self.assertNotIn("Dependency migration checklist", result.recommendations[0].rationale)
        client.decide.assert_not_called()

    def test_unknown_signal_fails_closed_without_guidance_or_remote_call(self):
        client = Mock()
        result = choose_strategies(
            TaskKind.CODING,
            [TaskSignal.DEPENDENCY_CHANGE, "unknown-private-signal"],
            client=client,
        )

        self.assertEqual(result.recommendations, ())
        self.assertEqual(result.status, "no-remote-choice")
        client.decide.assert_not_called()


if __name__ == "__main__":
    unittest.main()
