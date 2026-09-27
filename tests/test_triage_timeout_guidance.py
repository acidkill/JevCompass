"""Focused tests for timeout triage guidance sequencing."""

from __future__ import annotations

import unittest

from jevcompass.triage import (
    FailureKind,
    HypothesisId,
    REMOTE_CHOICE,
    TimeoutObservation,
    triage_failure,
)


class FakeClient:
    def __init__(self):
        self.calls = []

    def decide(self, state, questions):
        self.calls.append((state, questions))
        return {
            "diagnostic": {
                "type": "choice",
                "choice": HypothesisId.TIMEOUT_NONTERMINATING.value,
                "confidence": 0.9,
            }
        }


class TimeoutGuidanceTests(unittest.TestCase):
    def test_verified_local_wait_observation_gives_bounded_validation_sequence(self):
        client = FakeClient()
        result = triage_failure(
            [FailureKind.TIMEOUT],
            [HypothesisId.TIMEOUT_NONTERMINATING, HypothesisId.TIMEOUT_CONTENTION],
            1,
            client,
            timeout_observations=[TimeoutObservation.WAIT_CONDITION_UNSATISFIABLE],
        )

        self.assertEqual(result.steps[0].id, HypothesisId.TIMEOUT_NONTERMINATING)
        self.assertEqual(result.status, "no-remote-choice")
        self.assertEqual(client.calls, [])
        instruction = result.steps[0].instruction.lower()
        for required_phrase in (
            "wait condition can be satisfied on every path",
            "local observations mark it unsatisfiable",
            "written contract",
            "lead, not a confirmed diagnosis",
            "minimal source fix",
            "rerun the same focused check that failed",
            "passing rerun confirms only that check",
            "every mandatory validation",
        ):
            with self.subTest(required_phrase=required_phrase):
                self.assertIn(required_phrase, instruction)

    def test_remote_choice_of_same_timeout_id_uses_identical_guidance(self):
        client = FakeClient()
        result = triage_failure(
            [FailureKind.TIMEOUT],
            [HypothesisId.TIMEOUT_NONTERMINATING, HypothesisId.TIMEOUT_CONTENTION],
            1,
            client,
        )

        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.steps[0].id, HypothesisId.TIMEOUT_NONTERMINATING)
        self.assertEqual(len(client.calls), 1)
        self.assertNotIn("locally verified", result.steps[0].instruction.lower())
        self.assertIn("if local observations mark it unsatisfiable",
                      result.steps[0].instruction.lower())

        locally_resolved = triage_failure(
            [FailureKind.TIMEOUT],
            [HypothesisId.TIMEOUT_NONTERMINATING, HypothesisId.TIMEOUT_CONTENTION],
            1,
            FakeClient(),
            timeout_observations=[TimeoutObservation.WAIT_CONDITION_UNSATISFIABLE],
        )
        self.assertEqual(
            result.steps[0].instruction,
            locally_resolved.steps[0].instruction,
        )


if __name__ == "__main__":
    unittest.main()
