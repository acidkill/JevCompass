"""Tests for normalized target metadata in remote eligibility decisions."""

from __future__ import annotations

import unittest

from jevcompass.test_order import (
    DecisionReason,
    NO_REMOTE_CHOICE,
    REMOTE_CHOICE,
    RequiredTest,
    rank_tests,
)


class RecordingClient:
    def __init__(self):
        self.calls = []

    def decide(self, state, questions):
        self.calls.append((state, questions))
        return {"first": {"type": "choice", "choice": "t2", "confidence": 0.9}}


def pair(targets_a, targets_b, *, kinds=("unit", "unit")):
    return [
        {"id": "local-a", "kind": kinds[0], "command": "private a",
         "relevance": 0.5, "coverage_targets": targets_a},
        {"id": "local-b", "kind": kinds[1], "command": "private b",
         "relevance": 0.5, "coverage_targets": targets_b},
    ]


class TargetEligibilityTests(unittest.TestCase):
    def test_distinct_targets_make_same_kind_candidates_eligible(self):
        client = RecordingClient()
        required = [RequiredTest("mandatory full suite", "full")]

        result = rank_tests(
            "api",
            pair(("public_contract_changed",), ("internal_logic_changed",)),
            required,
            client,
        )

        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.decision_reason, DecisionReason.ACCEPTED)
        self.assertEqual(result.ordered_ids[0], "local-b")
        self.assertEqual(result.required, tuple(required))
        self.assertEqual(len(client.calls), 1)
        state, _ = client.calls[0]
        self.assertEqual(
            [candidate["coverage_targets"] for candidate in state["candidates"]],
            [["public_contract_changed"], ["internal_logic_changed"]],
        )

    def test_same_normalized_target_sets_do_not_force_remote_choice(self):
        client = RecordingClient()
        supplied = pair(
            ("public_contract_changed", "internal_logic_changed",
             "public_contract_changed"),
            ("internal_logic_changed", " PUBLIC_CONTRACT_CHANGED "),
        )

        result = rank_tests("api", supplied, [], client)

        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(result.decision_reason, DecisionReason.NO_CHOICE_NEEDED)
        self.assertEqual(client.calls, [])

    def test_legacy_unit_before_contract_still_applies_without_targets(self):
        client = RecordingClient()
        result = rank_tests(
            "python",
            [
                {"kind": "unit", "command": "private unit", "relevance": 0.5},
                {"kind": "contract", "command": "private contract", "relevance": 0.5},
            ],
            [],
            client,
        )

        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(result.ordered_candidates[0].kind.value, "unit")
        self.assertEqual(result.decision_reason, DecisionReason.LOCAL_RESOLUTION)
        self.assertEqual(client.calls, [])

    def test_targets_bypass_legacy_shortcut_and_invalid_targets_still_skip(self):
        client = RecordingClient()
        result = rank_tests(
            "python",
            pair(("public_contract_changed",), ("internal_logic_changed",),
                 kinds=("unit", "contract")),
            [],
            client,
        )
        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(len(client.calls), 1)

        invalid_client = RecordingClient()
        required = [RequiredTest("mandatory suite", "full")]
        invalid = pair(("unknown-private-target",), ("internal_logic_changed",))
        invalid_result = rank_tests("api", invalid, required, invalid_client)
        self.assertEqual(invalid_result.status, NO_REMOTE_CHOICE)
        self.assertIsNone(invalid_result.decision_reason)
        self.assertEqual(invalid_result.required, tuple(required))
        self.assertEqual(invalid_client.calls, [])


if __name__ == "__main__":
    unittest.main()
