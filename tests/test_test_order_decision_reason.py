"""Tests for bounded test-order decision reasons."""
from __future__ import annotations

import unittest

from jevcompass.test_order import (
    ChangedSurface,
    DecisionReason,
    NO_REMOTE_CHOICE,
    REMOTE_CHOICE,
    RequiredTest,
    rank_tests,
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


def ambiguous_candidates():
    return [
        {
            "id": "/private/unit-id",
            "kind": "unit",
            "command": "pytest /private/repo/test_unit.py --token=private",
            "coverage": "direct",
            "runtime": "slow",
            "relevance": 0.5,
        },
        {
            "id": "private-contract-id",
            "kind": "contract",
            "command": "pytest /private/repo/test_contract.py",
            "coverage": "indirect",
            "runtime": "fast",
            "relevance": 0.5,
        },
    ]


class TestOrderDecisionReasonTests(unittest.TestCase):
    def rank(self, answer=None, error=None):
        client = FakeClient(answer=answer, error=error)
        result = rank_tests(
            ChangedSurface.API,
            ambiguous_candidates(),
            [RequiredTest("pytest /private/repo/full.py", "private-required-id")],
            client,
        )
        return result, client

    def test_accepted_remote_choice_has_bounded_reason_and_keeps_order(self):
        result, client = self.rank({
            "first": {"type": "choice", "choice": "t2", "confidence": 0.9},
        })
        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.decision_reason, DecisionReason.ACCEPTED)
        self.assertEqual(result.ordered_ids, ("private-contract-id", "/private/unit-id"))
        self.assertEqual(result.required[0].required_id, "private-required-id")
        self.assertEqual(len(client.calls), 1)

    def test_low_confidence_falls_back_with_insufficient_confidence_reason(self):
        result, client = self.rank({
            "first": {"type": "choice", "choice": "t2", "confidence": 0.69},
        })
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(result.decision_reason, DecisionReason.INSUFFICIENT_CONFIDENCE)
        self.assertEqual(result.ordered_ids, ("/private/unit-id", "private-contract-id"))
        self.assertEqual(len(client.calls), 1)

    def test_malformed_response_uses_invalid_response_reason(self):
        for answer in (
            None,
            {"first": {"type": "rank", "choice": "t2", "confidence": 0.9}},
            {"unexpected": {"type": "choice", "choice": "t2", "confidence": 0.9}},
            {"first": {"type": "choice", "choice": "t2", "confidence": "0.9"}},
            {"first": {"type": "choice", "choice": "t2", "confidence": 1.01}},
        ):
            with self.subTest(answer=answer):
                result, client = self.rank(answer)
                self.assertEqual(result.status, NO_REMOTE_CHOICE)
                self.assertEqual(result.decision_reason, DecisionReason.INVALID_RESPONSE)
                self.assertEqual(result.ordered_ids, ("/private/unit-id", "private-contract-id"))
                self.assertEqual(len(client.calls), 1)

    def test_unknown_candidate_id_has_distinct_reason(self):
        result, client = self.rank({
            "first": {"type": "choice", "choice": "not-a-candidate", "confidence": 0.9},
        })
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(result.decision_reason, DecisionReason.UNKNOWN_CHOICE)
        self.assertEqual(result.ordered_ids, ("/private/unit-id", "private-contract-id"))
        self.assertEqual(len(client.calls), 1)

    def test_local_no_choice_and_local_resolution_do_not_call_client(self):
        singleton_client = FakeClient()
        singleton = rank_tests("api", [
            {"id": "only", "kind": "unit", "command": "local command"},
        ], [], singleton_client)
        self.assertEqual(singleton.status, NO_REMOTE_CHOICE)
        self.assertEqual(singleton.decision_reason, DecisionReason.NO_CHOICE_NEEDED)
        self.assertEqual(singleton_client.calls, [])

        resolved_client = FakeClient()
        local = rank_tests("api", [
            {"id": "direct", "kind": "unit", "command": "unit",
             "coverage": "direct", "runtime": "fast", "relevance": 0.5},
            {"id": "indirect", "kind": "contract", "command": "contract",
             "coverage": "indirect", "runtime": "slow", "relevance": 0.5},
        ], [], resolved_client)
        self.assertEqual(local.status, NO_REMOTE_CHOICE)
        self.assertEqual(local.decision_reason, DecisionReason.LOCAL_RESOLUTION)
        self.assertEqual(resolved_client.calls, [])

    def test_provider_timeout_has_safe_reason_without_exception_text(self):
        private_message = "private backend outage detail"
        result, client = self.rank(error=TimeoutError(private_message))
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(result.decision_reason, DecisionReason.PROVIDER_ERROR)
        self.assertNotIn(private_message, repr(result.decision_reason))
        self.assertEqual(len(client.calls), 1)

    def test_reason_never_contains_candidate_or_backend_text(self):
        private_message = "backend said private text"
        result, client = self.rank({
            "first": {
                "type": "choice",
                "choice": "unknown private choice",
                "confidence": 0.9,
                "message": private_message,
            },
        })
        self.assertEqual(result.decision_reason, DecisionReason.UNKNOWN_CHOICE)
        safe_reason = result.decision_reason.value
        for private_value in (
            private_message,
            "unknown private choice",
            "/private/repo",
            "private",
        ):
            self.assertNotIn(private_value, safe_reason)
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(len(client.calls), 1)

    def test_existing_positional_result_construction_keeps_optional_reason(self):
        from jevcompass.test_order import TestOrderResult

        result = TestOrderResult((), (), NO_REMOTE_CHOICE, None)
        self.assertIsNone(result.decision_reason)


class TestOrderReasonCliTests(unittest.TestCase):
    def test_cli_exposes_known_reason_and_required_checks_without_execution(self):
        import contextlib
        import io
        import json
        from jevcompass.cli import _display_test_order
        from jevcompass.test_order import TestOrderResult
        result = TestOrderResult((), (RequiredTest("python -m unittest", "ci"),), NO_REMOTE_CHOICE,
                                 decision_reason=DecisionReason.LOCAL_RESOLUTION)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(_display_test_order(result, True), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["decision_reason"], "local_resolution")
        self.assertEqual(payload["required"], [{"id": "ci", "command": "python -m unittest"}])
        self.assertFalse(payload["executed"])

    def test_unknown_reason_is_not_rendered_in_json_or_text(self):
        import contextlib
        import io
        import json
        from jevcompass.cli import _display_test_order
        from jevcompass.test_order import TestOrderResult
        result = TestOrderResult((), (), NO_REMOTE_CHOICE, decision_reason="private-backend-error")
        for as_json in (True, False):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                _display_test_order(result, as_json)
            self.assertNotIn("private-backend-error", output.getvalue())
            if as_json:
                self.assertIsNone(json.loads(output.getvalue())["decision_reason"])


if __name__ == "__main__":
    unittest.main()
