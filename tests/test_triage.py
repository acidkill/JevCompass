"""Offline contract tests for ambiguous test-failure triage."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from jevcompass.decisions import DecisionsClient, ENDPOINT
from jevcompass.triage import (
    CATALOG,
    DECISION_TIMEOUT,
    NO_REMOTE_CHOICE,
    REMOTE_CHOICE,
    FailureKind,
    HypothesisId,
    ImportObservation,
    triage_failure,
)


KIND = FailureKind.IMPORT
HYPOTHESES = (
    HypothesisId.IMPORT_MODULE_MISSING,
    HypothesisId.IMPORT_PATH_CHANGED,
)


class FakeClient:
    def __init__(self, answer=None, error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def decide(self, state, questions):
        self.calls.append((state, questions))
        if self.error is not None:
            raise self.error
        return self.answer


class TriageTests(unittest.TestCase):
    def test_success_empty_failure_and_single_plausible_hypothesis_skip_remote(self):
        for kinds, hypotheses, status in (
            ((KIND,), HYPOTHESES, 0),
            ((), HYPOTHESES, 1),
            ((KIND,), (), 1),
            ((KIND,), HYPOTHESES[:1], 1),
            ((FailureKind.TIMEOUT,), HYPOTHESES, 1),
        ):
            with self.subTest(kinds=kinds, hypotheses=hypotheses, status=status):
                client = FakeClient()
                result = triage_failure(kinds, hypotheses, status, client)
                self.assertEqual(result.status, NO_REMOTE_CHOICE)
                self.assertEqual(result.observed_exit_status, status)
                self.assertEqual(client.calls, [])

    def test_complete_import_observations_rule_out_missing_package_locally(self):
        client = FakeClient()
        result = triage_failure(
            (KIND,), HYPOTHESES, 1, client,
            import_observations=(
                ImportObservation.PACKAGE_PRESENT,
                ImportObservation.TARGET_MODULE_ABSENT,
                ImportObservation.REPLACEMENT_MODULE_PRESENT,
            ),
        )
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(result.observed_exit_status, 1)
        self.assertEqual(
            [step.id for step in result.steps],
            [HypothesisId.IMPORT_PATH_CHANGED],
        )
        self.assertEqual(client.calls, [])

    def test_contradictory_import_observations_abstain_without_remote_call(self):
        contradictory = (
            (ImportObservation.PACKAGE_PRESENT, ImportObservation.PACKAGE_ABSENT),
            (ImportObservation.PACKAGE_ABSENT, ImportObservation.TARGET_MODULE_PRESENT),
            (
                ImportObservation.PACKAGE_PRESENT,
                ImportObservation.TARGET_MODULE_ABSENT,
                ImportObservation.REPLACEMENT_MODULE_PRESENT,
                ImportObservation.REPLACEMENT_MODULE_ABSENT,
            ),
        )
        for observations in contradictory:
            with self.subTest(observations=observations):
                client = FakeClient()
                result = triage_failure(
                    (KIND,), HYPOTHESES, 1, client,
                    import_observations=observations,
                )
                self.assertEqual(result.status, NO_REMOTE_CHOICE)
                self.assertEqual(result.steps, ())
                self.assertEqual(client.calls, [])

    def test_import_observations_require_enum_tokens(self):
        for observations in (
            ("package_present",),
            ("/private/repo/parcelcache",),
            "package_present",
        ):
            client = FakeClient()
            with self.subTest(observations=observations), self.assertRaises(TypeError):
                triage_failure(
                    (KIND,), HYPOTHESES, 1, client,
                    import_observations=observations,
                )
            self.assertEqual(client.calls, [])

    def test_non_import_failure_keeps_existing_remote_behavior_without_evidence(self):
        hypotheses = (
            HypothesisId.TIMEOUT_CONTENTION,
            HypothesisId.TIMEOUT_NONTERMINATING,
        )
        client = FakeClient({
            "diagnostic": {
                "type": "choice",
                "choice": hypotheses[0].value,
                "confidence": 0.9,
            },
        })
        result = triage_failure((FailureKind.TIMEOUT,), hypotheses, 1, client)
        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(len(client.calls), 1)

    def test_input_requires_enum_values_and_integer_exit_status(self):
        for args in (
            (("import",), HYPOTHESES, 1),
            ((KIND,), ("import_module_missing", "import_path_changed"), 1),
            ((KIND,), HYPOTHESES, True),
            ((KIND,), HYPOTHESES, "1"),
        ):
            client = FakeClient()
            with self.subTest(args=args), self.assertRaises(TypeError):
                triage_failure(*args, client)
            self.assertEqual(client.calls, [])

    def test_remote_choice_uses_exact_local_ids_and_returns_local_steps(self):
        client = FakeClient({
            "diagnostic": {
                "type": "choice",
                "choice": HypothesisId.IMPORT_PATH_CHANGED.value,
                "confidence": 0.91,
            },
        })
        result = triage_failure((KIND,), HYPOTHESES, 2, client)

        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.observed_exit_status, 2)
        self.assertTrue(result.test_failed)
        self.assertEqual([step.id for step in result.steps], [
            HypothesisId.IMPORT_PATH_CHANGED,
            HypothesisId.IMPORT_MODULE_MISSING,
        ])
        self.assertEqual(len(result.steps), 2)
        state, questions = client.calls[0]
        self.assertEqual(state, {
            "test_outcome": "failed",
            "failure_kinds": ["import"],
            "hypotheses": ["import_module_missing", "import_path_changed"],
        })
        self.assertEqual(set(questions["diagnostic"]["criteria"]), {
            item.value for item in HYPOTHESES
        })
        self.assertTrue(all(step == CATALOG[step.id].step for step in result.steps))

    def test_request_is_allowlisted_and_contains_no_raw_diagnostics(self):
        private_values = (
            "secret prompt 7731", "/private/client/repo", "private traceback",
            "source-code-secret", "pytest --token=hidden", "parcelcache.codec",
            "parcelcache.wire", "api.py", "wire.py",
        )
        captured = {}

        def transport(url, body, api_key, timeout):
            captured.update(url=url, body=body.decode(), timeout=timeout)
            return json.dumps({"answers": {
                "diagnostic": {
                    "type": "choice",
                    "choice": "import_module_missing",
                    "confidence": 0.88,
                },
            }}).encode()

        client = DecisionsClient(
            api_key="test-key", model="test/model", timeout=DECISION_TIMEOUT,
            transport=transport,
        )
        result = triage_failure(
            (KIND,), HYPOTHESES, 1, client,
            import_observations=(
                ImportObservation.PACKAGE_PRESENT,
                ImportObservation.TARGET_MODULE_ABSENT,
            ),
        )

        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(captured["url"], ENDPOINT)
        self.assertEqual(captured["timeout"], DECISION_TIMEOUT)
        request = json.loads(captured["body"])
        self.assertEqual(request["state"], {
            "test_outcome": "failed",
            "failure_kinds": ["import"],
            "hypotheses": ["import_module_missing", "import_path_changed"],
            "import_observations": ["package_present", "target_module_absent"],
        })
        encoded = json.dumps(request)
        for private_value in private_values:
            self.assertNotIn(private_value, encoded)

    def test_malformed_unknown_extra_and_low_confidence_answers_fall_back(self):
        answers = (
            None,
            {"diagnostic": {"type": "rank", "choice": HYPOTHESES[0].value, "confidence": 0.99}},
            {"diagnostic": {"type": "choice", "choice": "unknown", "confidence": 0.99}},
            {"diagnostic": {"type": "choice", "choice": HYPOTHESES[0].value, "confidence": 0.69}},
            {"diagnostic": {"type": "choice", "choice": HYPOTHESES[0].value, "confidence": True}},
            {"diagnostic": {"type": "choice", "choice": HYPOTHESES[0].value, "confidence": float("nan")}},
            {
                "diagnostic": {"type": "choice", "choice": HYPOTHESES[0].value, "confidence": 0.99},
                "extra": {"type": "choice", "choice": HYPOTHESES[1].value, "confidence": 0.99},
            },
        )
        expected = tuple(CATALOG[hypothesis].step for hypothesis in HYPOTHESES)
        for answer in answers:
            with self.subTest(answer=answer):
                result = triage_failure((KIND,), HYPOTHESES, 7, FakeClient(answer))
                self.assertEqual(result.status, NO_REMOTE_CHOICE)
                self.assertEqual(result.observed_exit_status, 7)
                self.assertEqual(result.steps, expected)
                self.assertTrue(result.test_failed)

    def test_timeout_falls_back_without_leaking_exception_details(self):
        secret = "private path /client/repo"
        result = triage_failure(
            (KIND,), HYPOTHESES, 1,
            FakeClient(error=TimeoutError(secret)),
        )
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertNotIn(secret, repr(result))

    def test_default_client_uses_bounded_timeout(self):
        client = FakeClient({
            "diagnostic": {
                "type": "choice",
                "choice": HYPOTHESES[0].value,
                "confidence": 0.9,
            },
        })
        with patch("jevcompass.triage.DecisionsClient", return_value=client) as factory:
            triage_failure((KIND,), HYPOTHESES, 1)
        factory.assert_called_once_with(timeout=DECISION_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
