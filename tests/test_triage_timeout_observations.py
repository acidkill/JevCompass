"""Verified local timeout-observation contracts for triage and CLI."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest import mock

from jevcompass.cli import main
from jevcompass.triage import (
    FailureKind,
    HypothesisId,
    NO_REMOTE_CHOICE,
    REMOTE_CHOICE,
    TimeoutObservation,
    triage_failure,
)


TIMEOUT_HYPOTHESES = (
    HypothesisId.TIMEOUT_CONTENTION,
    HypothesisId.TIMEOUT_NONTERMINATING,
)


class RecordingClient:
    def __init__(self, choice=HypothesisId.TIMEOUT_NONTERMINATING):
        self.choice = choice
        self.calls = []

    def decide(self, state, questions):
        self.calls.append((state, questions))
        return {
            "diagnostic": {
                "type": "choice",
                "choice": self.choice.value,
                "confidence": 0.9,
            },
        }


class TimeoutObservationTriageTests(unittest.TestCase):
    def test_unsatisfiable_wait_selects_existing_local_next_check(self):
        client = RecordingClient()
        result = triage_failure(
            (FailureKind.TIMEOUT,), TIMEOUT_HYPOTHESES, 124, client,
            timeout_observations=(TimeoutObservation.WAIT_CONDITION_UNSATISFIABLE,),
        )

        self.assertEqual(result.observed_exit_status, 124)
        self.assertTrue(result.test_failed)
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(
            [step.id for step in result.steps],
            [HypothesisId.TIMEOUT_NONTERMINATING],
        )
        self.assertIn("Check whether", result.steps[0].instruction)
        self.assertFalse(hasattr(result, "confirmed_cause"))
        self.assertEqual(client.calls, [])

    def test_contention_selection_requires_progress_and_satisfiable_wait(self):
        client = RecordingClient()
        result = triage_failure(
            (FailureKind.TIMEOUT,), TIMEOUT_HYPOTHESES, 9, client,
            timeout_observations=(
                TimeoutObservation.RESOURCE_CONTENTION_OBSERVED,
                TimeoutObservation.PROGRESS_OBSERVED,
                TimeoutObservation.WAIT_CONDITION_SATISFIABLE,
            ),
        )

        self.assertEqual(result.observed_exit_status, 9)
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(
            [step.id for step in result.steps],
            [HypothesisId.TIMEOUT_CONTENTION],
        )
        self.assertIn("Review whether", result.steps[0].instruction)
        self.assertEqual(client.calls, [])

    def test_ineligible_local_selection_does_not_add_candidate(self):
        client = RecordingClient()
        result = triage_failure(
            (FailureKind.TIMEOUT,),
            (HypothesisId.TIMEOUT_CONTENTION,),
            5,
            client,
            timeout_observations=(TimeoutObservation.WAIT_CONDITION_UNSATISFIABLE,),
        )

        self.assertEqual(result.observed_exit_status, 5)
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(
            [step.id for step in result.steps],
            [HypothesisId.TIMEOUT_CONTENTION],
        )
        self.assertEqual(client.calls, [])

    def test_each_contradictory_pair_abstains_without_client_call(self):
        contradictions = (
            (
                TimeoutObservation.PROGRESS_OBSERVED,
                TimeoutObservation.NO_PROGRESS_OBSERVED,
            ),
            (
                TimeoutObservation.RESOURCE_CONTENTION_OBSERVED,
                TimeoutObservation.NO_RESOURCE_CONTENTION_OBSERVED,
            ),
            (
                TimeoutObservation.WAIT_CONDITION_SATISFIABLE,
                TimeoutObservation.WAIT_CONDITION_UNSATISFIABLE,
            ),
        )
        for observations in contradictions:
            with self.subTest(observations=observations):
                client = RecordingClient()
                result = triage_failure(
                    (FailureKind.TIMEOUT,), TIMEOUT_HYPOTHESES, 17, client,
                    timeout_observations=observations,
                )

                self.assertEqual(result.observed_exit_status, 17)
                self.assertTrue(result.test_failed)
                self.assertEqual(result.status, NO_REMOTE_CHOICE)
                self.assertEqual(result.steps, ())
                self.assertEqual(client.calls, [])

    def test_partial_facts_are_sent_as_exact_allowlisted_state(self):
        observations = (
            TimeoutObservation.RESOURCE_CONTENTION_OBSERVED,
            TimeoutObservation.PROGRESS_OBSERVED,
        )
        client = RecordingClient()
        result = triage_failure(
            (FailureKind.TIMEOUT,), TIMEOUT_HYPOTHESES, 3, client,
            timeout_observations=observations,
        )

        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.observed_exit_status, 3)
        self.assertEqual(len(client.calls), 1)
        state, questions = client.calls[0]
        self.assertEqual(state, {
            "test_outcome": "failed",
            "failure_kinds": ["timeout"],
            "hypotheses": ["timeout_contention", "timeout_nonterminating"],
            "timeout_observations": [
                "resource_contention_observed",
                "progress_observed",
            ],
        })
        self.assertEqual(
            set(questions["diagnostic"]["criteria"]),
            {"timeout_contention", "timeout_nonterminating"},
        )

    def test_timeout_observations_require_enum_tokens_without_echoing_values(self):
        invalid_values = (
            ("progress_observed",),
            ("/private/client/repo",),
            "progress_observed",
        )
        for observations in invalid_values:
            with self.subTest(observations=observations):
                client = RecordingClient()
                with self.assertRaisesRegex(TypeError, "timeout-observations-must-be-enums"):
                    triage_failure(
                        (FailureKind.TIMEOUT,), TIMEOUT_HYPOTHESES, 1, client,
                        timeout_observations=observations,
                    )
                self.assertEqual(client.calls, [])

    def test_non_timeout_failure_omits_valid_timeout_facts(self):
        client = RecordingClient(HypothesisId.ASSERTION_BEHAVIOR_REGRESSION)
        result = triage_failure(
            (FailureKind.ASSERTION,),
            (
                HypothesisId.ASSERTION_EXPECTATION_DRIFT,
                HypothesisId.ASSERTION_BEHAVIOR_REGRESSION,
            ),
            4,
            client,
            timeout_observations=(TimeoutObservation.PROGRESS_OBSERVED,),
        )

        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(len(client.calls), 1)
        state, _ = client.calls[0]
        self.assertEqual(state, {
            "test_outcome": "failed",
            "failure_kinds": ["assertion"],
            "hypotheses": [
                "assertion_expectation_drift",
                "assertion_behavior_regression",
            ],
        })
        self.assertNotIn("timeout_observations", state)

    def test_cli_repeatable_timeout_observations_are_allowlisted(self):
        output = io.StringIO()
        with mock.patch("jevcompass.triage.DecisionsClient") as client, \
             contextlib.redirect_stdout(output):
            client.return_value.decide.return_value = {
                "diagnostic": {
                    "type": "choice",
                    "choice": "timeout_nonterminating",
                    "confidence": 0.9,
                },
            }
            exit_code = main([
                "triage", "--exit-code", "1", "--kind", "timeout",
                "--hypothesis", "timeout_contention",
                "--hypothesis", "timeout_nonterminating",
                "--timeout-observation", "resource_contention_observed",
                "--timeout-observation", "progress_observed",
                "--json",
            ])

        result = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(result["test_failed"])
        self.assertEqual(result["observed_exit_status"], 1)
        self.assertEqual(result["status"], REMOTE_CHOICE)
        self.assertNotIn("confirmed_cause", result)
        state = client.return_value.decide.call_args.args[0]
        self.assertEqual(state, {
            "test_outcome": "failed",
            "failure_kinds": ["timeout"],
            "hypotheses": ["timeout_contention", "timeout_nonterminating"],
            "timeout_observations": [
                "resource_contention_observed",
                "progress_observed",
            ],
        })

    def test_cli_rejects_private_timeout_value_before_client_call(self):
        stderr = io.StringIO()
        with (
            mock.patch("jevcompass.triage.DecisionsClient") as client,
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as caught,
        ):
            main([
                "triage", "--exit-code", "1", "--kind", "timeout",
                "--hypothesis", "timeout_contention",
                "--hypothesis", "timeout_nonterminating",
                "--timeout-observation", "/private/client/repo",
                "--json",
            ])

        self.assertEqual(caught.exception.code, 2)
        self.assertIn("invalid choice", stderr.getvalue())
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
