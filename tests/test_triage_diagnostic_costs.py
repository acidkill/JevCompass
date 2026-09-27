from __future__ import annotations

from types import MappingProxyType
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from jevcompass.decisions import DecisionsClient
from jevcompass.triage import (
    NO_REMOTE_CHOICE,
    REMOTE_CHOICE,
    DiagnosticCost,
    FailureKind,
    HypothesisId,
    ImportObservation,
    TriageDecisionReason,
    triage_failure,
)


HYPOTHESES = (
    HypothesisId.IMPORT_MODULE_MISSING,
    HypothesisId.IMPORT_PATH_CHANGED,
)


class FakeClient:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def decide(self, state, questions):
        self.calls.append((state, questions))
        return self.answers


def valid_diagnostic(choice: HypothesisId):
    return {
        "diagnostic": {
            "type": "choice",
            "choice": choice.value,
            "confidence": 0.9,
        }
    }


class DiagnosticCostTests(unittest.TestCase):
    def test_omitted_costs_preserve_the_default_request_exactly(self):
        default_client = FakeClient(valid_diagnostic(HYPOTHESES[0]))
        none_client = FakeClient(valid_diagnostic(HYPOTHESES[0]))
        empty_client = FakeClient(valid_diagnostic(HYPOTHESES[0]))
        triage_failure((FailureKind.IMPORT,), HYPOTHESES, 1, default_client)
        triage_failure(
            (FailureKind.IMPORT,), HYPOTHESES, 1, none_client,
            diagnostic_costs=None,
        )
        triage_failure(
            (FailureKind.IMPORT,), HYPOTHESES, 1, empty_client,
            diagnostic_costs={},
        )
        default_request = default_client.calls[0]
        self.assertEqual(default_request, none_client.calls[0])
        self.assertEqual(default_request, empty_client.calls[0])
        self.assertNotIn("diagnostic_costs", default_request[0])

    def test_cost_tokens_are_safe_and_change_only_diagnostic_request_metadata(self):
        costs = MappingProxyType({
            HYPOTHESES[0]: DiagnosticCost.LOW,
            HYPOTHESES[1]: DiagnosticCost.HIGH,
        })
        baseline_client = FakeClient(valid_diagnostic(HYPOTHESES[0]))
        baseline = triage_failure(
            (FailureKind.IMPORT,), HYPOTHESES, 1, baseline_client,
            rank_hypotheses=True,
        )
        client = FakeClient(valid_diagnostic(HYPOTHESES[0]))
        result = triage_failure(
            (FailureKind.IMPORT,), HYPOTHESES, 1, client,
            rank_hypotheses=True, diagnostic_costs=costs,
        )

        state, questions = client.calls[0]
        baseline_state, baseline_questions = baseline_client.calls[0]
        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.hypothesis_ranking_status, "incomplete")
        self.assertEqual(result.hypothesis_order, ())
        self.assertEqual(
            state["diagnostic_costs"],
            {
                HYPOTHESES[0].value: "low",
                HYPOTHESES[1].value: "high",
            },
        )
        self.assertIn("relative diagnostic cost: low", questions["diagnostic"]["criteria"][HYPOTHESES[0].value])
        self.assertIn("relative diagnostic cost: high", questions["diagnostic"]["criteria"][HYPOTHESES[1].value])
        self.assertIn("not evidence of causal likelihood", questions["diagnostic"]["instructions"])
        self.assertEqual(
            questions["hypothesis_pair_0_1"],
            baseline_questions["hypothesis_pair_0_1"],
        )
        self.assertNotIn("diagnostic_costs", baseline_state)
        self.assertNotIn("cost", questions["hypothesis_pair_0_1"]["instructions"].lower())
        self.assertNotIn("diagnostic_costs", questions["hypothesis_pair_0_1"])

        reverse_client = FakeClient(valid_diagnostic(HYPOTHESES[0]))
        triage_failure(
            (FailureKind.IMPORT,), HYPOTHESES, 1, reverse_client,
            diagnostic_costs={
                HYPOTHESES[0]: DiagnosticCost.HIGH,
                HYPOTHESES[1]: DiagnosticCost.LOW,
            },
        )
        reverse_state, _ = reverse_client.calls[0]
        self.assertEqual(reverse_state["diagnostic_costs"], {
            HYPOTHESES[0].value: "high",
            HYPOTHESES[1].value: "low",
        })

    def test_changed_costs_miss_real_typed_cache_and_same_cost_hits(self):
        calls = []

        def transport(*_args):
            calls.append(1)
            return json.dumps({
                "answers": valid_diagnostic(HYPOTHESES[0]),
            }).encode()

        decision_client = DecisionsClient(api_key="synthetic", transport=transport)
        with tempfile.TemporaryDirectory() as cache_dir:
            with patch.dict(os.environ, {
                "JEVCOMPASS_TYPED_DECISION_CACHE": "1",
                "JEVCOMPASS_TYPED_CACHE_DIR": cache_dir,
            }), patch(
                "jevcompass.triage.DecisionsClient",
                return_value=decision_client,
            ):
                low_first = triage_failure(
                    (FailureKind.IMPORT,), HYPOTHESES, 1,
                    diagnostic_costs={
                        HYPOTHESES[0]: DiagnosticCost.LOW,
                        HYPOTHESES[1]: DiagnosticCost.HIGH,
                    },
                )
                reversed_costs = triage_failure(
                    (FailureKind.IMPORT,), HYPOTHESES, 1,
                    diagnostic_costs={
                        HYPOTHESES[0]: DiagnosticCost.HIGH,
                        HYPOTHESES[1]: DiagnosticCost.LOW,
                    },
                )
                repeated_reversed_costs = triage_failure(
                    (FailureKind.IMPORT,), HYPOTHESES, 1,
                    diagnostic_costs={
                        HYPOTHESES[0]: DiagnosticCost.HIGH,
                        HYPOTHESES[1]: DiagnosticCost.LOW,
                    },
                )

        self.assertEqual(low_first.status, REMOTE_CHOICE)
        self.assertFalse(low_first.cache_hit)
        self.assertEqual(reversed_costs.status, REMOTE_CHOICE)
        self.assertFalse(reversed_costs.cache_hit)
        self.assertTrue(repeated_reversed_costs.cache_hit)
        self.assertEqual(len(calls), 2)

    def test_invalid_cost_metadata_is_rejected_before_network(self):
        cases = (
            (["import_module_missing", "low"], TypeError),
            ({"import_module_missing": DiagnosticCost.LOW}, TypeError),
            ({HYPOTHESES[0]: "low"}, TypeError),
            ({HypothesisId.COLLECTION_SYNTAX: DiagnosticCost.LOW}, ValueError),
        )
        for costs, error in cases:
            client = FakeClient(valid_diagnostic(HYPOTHESES[0]))
            with self.subTest(costs=costs), self.assertRaises(error):
                triage_failure(
                    (FailureKind.IMPORT,), HYPOTHESES, 1, client,
                    diagnostic_costs=costs,
                )
            self.assertEqual(client.calls, [])

    def test_local_resolution_still_returns_before_remote_with_costs(self):
        client = FakeClient(valid_diagnostic(HYPOTHESES[0]))
        result = triage_failure(
            (FailureKind.IMPORT,), HYPOTHESES, 1, client,
            import_observations=(
                ImportObservation.PACKAGE_PRESENT,
                ImportObservation.TARGET_MODULE_ABSENT,
                ImportObservation.REPLACEMENT_MODULE_PRESENT,
            ),
            diagnostic_costs={
                HYPOTHESES[0]: DiagnosticCost.LOW,
                HYPOTHESES[1]: DiagnosticCost.HIGH,
            },
        )
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(
            [step.id for step in result.steps],
            [HypothesisId.IMPORT_PATH_CHANGED],
        )
        self.assertEqual(client.calls, [])

    def test_costs_do_not_reorder_fallback_after_malformed_provider_response(self):
        client = FakeClient({
            "diagnostic": {
                "type": "choice",
                "choice": "unknown-hypothesis",
                "confidence": 0.99,
            },
        })
        result = triage_failure(
            (FailureKind.IMPORT,), HYPOTHESES, 1, client,
            diagnostic_costs={
                HYPOTHESES[0]: DiagnosticCost.HIGH,
                HYPOTHESES[1]: DiagnosticCost.LOW,
            },
        )
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(result.decision_reason, TriageDecisionReason.INVALID_RESPONSE)
        self.assertEqual([step.id for step in result.steps], list(HYPOTHESES))
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
