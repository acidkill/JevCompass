"""Offline contract tests for optional pairwise hypothesis ordering."""

import contextlib
import io
import json
import unittest
from unittest import mock

from jevcompass.cli import main
from jevcompass.decisions import DecisionsClient
from jevcompass.triage import (
    AssertionObservation,
    FailureKind,
    HypothesisId,
    REMOTE_CHOICE,
    triage_failure,
)


HYPOTHESES = (
    HypothesisId.ASSERTION_EXPECTATION_DRIFT,
    HypothesisId.ASSERTION_BEHAVIOR_REGRESSION,
    HypothesisId.CONFIRM_BEHAVIOR_CONTRACT,
)
CAUSAL_HYPOTHESES = HYPOTHESES[:2]


def pairwise_answers(order):
    positions = {item.value: index for index, item in enumerate(order)}
    answers = {
        "diagnostic": {
            "type": "choice",
            "choice": HypothesisId.CONFIRM_BEHAVIOR_CONTRACT.value,
            "confidence": 0.91,
        }
    }
    for left_index, left in enumerate(CAUSAL_HYPOTHESES):
        for right_index in range(left_index + 1, len(CAUSAL_HYPOTHESES)):
            right = CAUSAL_HYPOTHESES[right_index]
            key = f"hypothesis_pair_{left_index}_{right_index}"
            winner = left if positions[left.value] < positions[right.value] else right
            answers[key] = {
                "type": "choice",
                "choice": winner.value,
                "confidence": 0.9,
            }
    return answers


class FakeClient:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def decide(self, state, questions):
        self.calls.append((state, questions))
        return self.answers


class HypothesisRankTests(unittest.TestCase):
    def test_confirmation_is_diagnostic_only_and_causal_order_excludes_it(self):
        expected_order = (
            HypothesisId.ASSERTION_BEHAVIOR_REGRESSION,
            HypothesisId.ASSERTION_EXPECTATION_DRIFT,
        )
        client = FakeClient(pairwise_answers(expected_order))
        result = triage_failure(
            (FailureKind.ASSERTION,), HYPOTHESES, 1, client, rank_hypotheses=True
        )

        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.hypothesis_ranking_status, "complete")
        self.assertEqual(result.hypothesis_order, expected_order)
        self.assertNotIn(HypothesisId.CONFIRM_BEHAVIOR_CONTRACT, result.hypothesis_order)
        self.assertEqual(result.steps[0].id, HypothesisId.CONFIRM_BEHAVIOR_CONTRACT)
        self.assertEqual(len(client.calls), 1)
        state, questions = client.calls[0]
        self.assertEqual(state["hypotheses"], [item.value for item in CAUSAL_HYPOTHESES])
        self.assertEqual(set(questions), {"diagnostic", "hypothesis_pair_0_1"})
        self.assertEqual(
            set(questions["diagnostic"]["criteria"]),
            {item.value for item in HYPOTHESES},
        )
        self.assertEqual(
            set(questions["hypothesis_pair_0_1"]["criteria"]),
            {item.value for item in CAUSAL_HYPOTHESES},
        )
        self.assertTrue(all(question["type"] == "choice" for question in questions.values()))

    def test_one_causal_candidate_keeps_diagnostic_choice_without_claiming_rank(self):
        hypotheses = (
            HypothesisId.ASSERTION_BEHAVIOR_REGRESSION,
            HypothesisId.CONFIRM_BEHAVIOR_CONTRACT,
        )
        answers = {
            "diagnostic": {
                "type": "choice",
                "choice": HypothesisId.CONFIRM_BEHAVIOR_CONTRACT.value,
                "confidence": 0.91,
            }
        }
        client = FakeClient(answers)
        result = triage_failure(
            (FailureKind.ASSERTION,), hypotheses, 1, client, rank_hypotheses=True
        )
        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.steps[0].id, HypothesisId.CONFIRM_BEHAVIOR_CONTRACT)
        self.assertEqual(result.hypothesis_ranking_status, "not_established")
        self.assertEqual(result.hypothesis_order, ())
        self.assertEqual(set(client.calls[0][1]), {"diagnostic"})

    def test_three_causal_hypotheses_cycle_suppresses_order_but_keeps_diagnostic(self):
        hypotheses = (
            HypothesisId.ASSERTION_EXPECTATION_DRIFT,
            HypothesisId.ASSERTION_BEHAVIOR_REGRESSION,
            HypothesisId.TIMEOUT_CONTENTION,
        )
        answers = {
            "diagnostic": {
                "type": "choice",
                "choice": HypothesisId.ASSERTION_EXPECTATION_DRIFT.value,
                "confidence": 0.91,
            },
            "hypothesis_pair_0_1": {
                "type": "choice",
                "choice": HypothesisId.ASSERTION_EXPECTATION_DRIFT.value,
                "confidence": 0.9,
            },
            "hypothesis_pair_0_2": {
                "type": "choice",
                "choice": HypothesisId.TIMEOUT_CONTENTION.value,
                "confidence": 0.9,
            },
            "hypothesis_pair_1_2": {
                "type": "choice",
                "choice": HypothesisId.ASSERTION_BEHAVIOR_REGRESSION.value,
                "confidence": 0.9,
            },
        }
        client = FakeClient(answers)
        result = triage_failure(
            (FailureKind.ASSERTION, FailureKind.TIMEOUT),
            hypotheses,
            1,
            client,
            rank_hypotheses=True,
        )

        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.decision_reason.value, "accepted")
        self.assertEqual(result.steps[0].id, HypothesisId.ASSERTION_EXPECTATION_DRIFT)
        self.assertEqual(result.hypothesis_ranking_status, "incomplete")
        self.assertEqual(result.hypothesis_order, ())
        _, questions = client.calls[0]
        self.assertEqual(set(questions), {
            "diagnostic",
            "hypothesis_pair_0_1",
            "hypothesis_pair_0_2",
            "hypothesis_pair_1_2",
        })
        self.assertNotIn(
            HypothesisId.CONFIRM_BEHAVIOR_CONTRACT.value,
            {
                option
                for question_id, question in questions.items()
                if question_id.startswith("hypothesis_pair_")
                for option in question["criteria"]
            },
        )

    def test_partial_or_malformed_ranking_keeps_valid_diagnostic_choice(self):
        answers = pairwise_answers(CAUSAL_HYPOTHESES)
        answers.pop("hypothesis_pair_0_1")
        result = triage_failure(
            (FailureKind.ASSERTION,), HYPOTHESES, 1, FakeClient(answers),
            rank_hypotheses=True,
        )
        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.decision_reason.value, "accepted")
        self.assertEqual(result.steps[0].id, HypothesisId.CONFIRM_BEHAVIOR_CONTRACT)
        self.assertEqual(result.hypothesis_ranking_status, "incomplete")
        self.assertEqual(result.hypothesis_order, ())

        answers = pairwise_answers(CAUSAL_HYPOTHESES)
        answers["hypothesis_pair_0_1"]["choice"] = ["malformed-choice"]
        result = triage_failure(
            (FailureKind.ASSERTION,), HYPOTHESES, 1, FakeClient(answers),
            rank_hypotheses=True,
        )
        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.steps[0].id, HypothesisId.CONFIRM_BEHAVIOR_CONTRACT)
        self.assertEqual(result.hypothesis_ranking_status, "incomplete")
        self.assertEqual(result.hypothesis_order, ())

        answers = pairwise_answers(CAUSAL_HYPOTHESES)
        answers["hypothesis_pair_0_1"]["confidence"] = 0.69
        result = triage_failure(
            (FailureKind.ASSERTION,), HYPOTHESES, 1, FakeClient(answers),
            rank_hypotheses=True,
        )
        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.steps[0].id, HypothesisId.CONFIRM_BEHAVIOR_CONTRACT)
        self.assertEqual(result.hypothesis_ranking_status, "incomplete")
        self.assertEqual(result.hypothesis_order, ())

    def test_local_resolution_skips_remote_and_leaves_order_unestablished(self):
        client = FakeClient({})
        result = triage_failure(
            (FailureKind.ASSERTION,), HYPOTHESES, 1, client,
            assertion_observations=(
                AssertionObservation.CONTRACT_UNDERSPECIFIED,
            ),
            rank_hypotheses=True,
        )
        self.assertEqual(result.hypothesis_ranking_status, "not_established")
        self.assertEqual(result.hypothesis_order, ())
        self.assertEqual(client.calls, [])

    def test_more_than_four_causal_candidates_skips_pairwise_ordering(self):
        client = FakeClient({
            "diagnostic": {
                "type": "choice",
                "choice": HypothesisId.ASSERTION_EXPECTATION_DRIFT.value,
                "confidence": 0.9,
            }
        })
        result = triage_failure(
            tuple(FailureKind), tuple(HypothesisId), 1, client,
            rank_hypotheses=True,
        )
        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.hypothesis_ranking_status, "not_established")
        self.assertEqual(result.hypothesis_order, ())
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(set(client.calls[0][1]), {"diagnostic"})

    def test_opt_in_ranked_request_bypasses_choice_only_cache(self):
        answers = pairwise_answers(CAUSAL_HYPOTHESES)
        payload = json.dumps({
            "answers": answers,
            "usage": {"input_tokens": 120, "output_tokens": 18, "cost": 0.00001},
        }).encode()
        calls = []
        real_client = DecisionsClient(
            api_key="synthetic", transport=lambda *args: (calls.append(args) or payload)
        )
        with mock.patch("jevcompass.triage.DecisionsClient", return_value=real_client), \
             mock.patch("jevcompass.triage.TypedDecisionCache") as cache:
            result = triage_failure(
                (FailureKind.ASSERTION,), HYPOTHESES, 1, rank_hypotheses=True
            )
        cache.assert_not_called()
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.hypothesis_ranking_status, "complete")
        self.assertEqual(result.hypothesis_order, CAUSAL_HYPOTHESES)

    def test_cli_opt_in_exposes_causal_order_and_default_schema_stays_legacy(self):
        output = io.StringIO()
        answers = pairwise_answers(CAUSAL_HYPOTHESES)
        with mock.patch("jevcompass.triage.DecisionsClient") as client, contextlib.redirect_stdout(output):
            client.return_value.decide.return_value = answers
            exit_code = main([
                "triage", "--exit-code", "1", "--kind", "assertion",
                *(arg for item in HYPOTHESES for arg in ("--hypothesis", item.value)),
                "--rank-hypotheses", "--json",
            ])
        self.assertEqual(exit_code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["hypothesis_ranking_status"], "complete")
        self.assertEqual(result["hypothesis_order"], [item.value for item in CAUSAL_HYPOTHESES])
        sent = client.return_value.decide.call_args.args
        self.assertEqual(set(sent[1]), {"diagnostic", "hypothesis_pair_0_1"})
        self.assertEqual(
            set(sent[1]["diagnostic"]["criteria"]),
            {item.value for item in HYPOTHESES},
        )

        output = io.StringIO()
        with mock.patch("jevcompass.triage.triage_failure") as triage, contextlib.redirect_stdout(output):
            from jevcompass.triage import TriageResult
            triage.return_value = TriageResult(1, (), "no-remote-choice")
            main(["triage", "--exit-code", "1", "--kind", "assertion",
                  "--hypothesis", HYPOTHESES[0].value, "--json"])
        default_result = json.loads(output.getvalue())
        self.assertEqual(default_result["hypothesis_ranking_status"], "not_established")
        self.assertNotIn("hypothesis_order", default_result)
        self.assertFalse(triage.call_args.kwargs["rank_hypotheses"])


if __name__ == "__main__":
    unittest.main()
