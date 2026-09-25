"""Offline tests for privacy-preserving test ordering."""

from __future__ import annotations

import json
import unittest

from jevcompass.test_order import (
    ChangedSurface,
    NO_REMOTE_CHOICE,
    REMOTE_CHOICE,
    RequiredTest,
    TestCandidate,
    TestKind,
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


class TestOrderTests(unittest.TestCase):
    def test_request_contains_only_allowlisted_metadata_and_opaque_ids(self):
        private_command = "pytest /private/client/repo/test_secret.py --token=command-secret"
        private_id = "/private/candidate/id"
        candidates = [
            {"id": private_id, "kind": "unit", "command": private_command, "relevance": 0.5},
            {"id": "source-code-secret", "kind": "integration",
             "command": "python private_integration.py", "relevance": 0.5},
        ]
        client = FakeClient({
            "first": {"type": "choice", "choice": "t1", "confidence": 0.91},
        })

        result = rank_tests("python", candidates, [], client)

        self.assertEqual(result.status, REMOTE_CHOICE)
        state, questions = client.calls[0]
        request = json.dumps((state, questions), sort_keys=True)
        for private_value in (private_command, private_id, "source-code-secret",
                              "command-secret", "0.5"):
            self.assertNotIn(private_value, request)
        self.assertEqual(state, {
            "changed_surface": "python",
            "candidates": [
                {"id": "t1", "kind": "unit", "descriptor": "unit test suite"},
                {"id": "t2", "kind": "integration",
                 "descriptor": "integration test suite"},
            ],
        })
        self.assertEqual(result.ordered_ids, (private_id, "source-code-secret"))

    def test_empty_and_singleton_candidates_do_not_call_client(self):
        for candidates in ([], [{"id": "only", "kind": "unit", "command": "pytest"}]):
            with self.subTest(candidates=candidates):
                client = FakeClient()
                result = rank_tests(ChangedSurface.API, candidates, [], client)
                self.assertEqual(result.status, NO_REMOTE_CHOICE)
                self.assertEqual(client.calls, [])

    def test_same_kind_commands_are_not_presented_as_meaningful_alternatives(self):
        client = FakeClient()
        result = rank_tests("api", [
            {"id": "one", "kind": "unit", "command": "pytest tests/a.py"},
            {"id": "two", "kind": "unit", "command": "pytest tests/b.py"},
        ], [], client)
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(client.calls, [])

    def test_clear_local_relevance_skips_remote_choice(self):
        client = FakeClient()
        result = rank_tests("python", [
            {"id": "direct", "kind": "unit", "command": "unit", "relevance": 0.95},
            {"id": "broad", "kind": "integration", "command": "integration", "relevance": 0.1},
        ], [], client)
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(result.ordered_ids, ("direct", "broad"))
        self.assertEqual(client.calls, [])

    def test_remote_choice_moves_candidate_first_and_preserves_required_commands(self):
        required = [
            {"id": "must-one", "command": "pytest tests/required_a.py"},
            RequiredTest("pytest tests/required_b.py", "must-two"),
        ]
        client = FakeClient({
            "first": {"type": "choice", "choice": "t2", "confidence": 0.88},
        })
        result = rank_tests("api", [
            {"id": "unit-id", "kind": "unit", "command": "pytest tests/unit.py",
             "relevance": 0.5},
            {"id": "contract-id", "kind": "contract", "command": "pytest tests/contract.py",
             "relevance": 0.5},
            {"id": "integration-id", "kind": "integration",
             "command": "pytest tests/integration.py", "relevance": 0.5},
        ], required, client)
        self.assertEqual(result.status, REMOTE_CHOICE)
        self.assertEqual(result.ordered_ids, ("contract-id", "unit-id", "integration-id"))
        self.assertEqual(result.required, tuple([
            RequiredTest("pytest tests/required_a.py", "must-one"),
            RequiredTest("pytest tests/required_b.py", "must-two"),
        ]))

    def test_generated_candidate_ids_remain_stable_after_remote_reordering(self):
        client = FakeClient({
            "first": {"type": "choice", "choice": "t2", "confidence": 0.9},
        })
        result = rank_tests("python", [
            TestCandidate(TestKind.UNIT, "unit"),
            TestCandidate(TestKind.CONTRACT, "contract"),
        ], [], client)
        self.assertEqual(result.ordered_ids, ("t2", "t1"))

    def test_low_confidence_returns_deterministic_local_order(self):
        client = FakeClient({
            "first": {"type": "choice", "choice": "t2", "confidence": 0.69},
        })
        result = rank_tests("python", [
            {"id": "first", "kind": "unit", "command": "unit", "relevance": 0.4},
            {"id": "second", "kind": "contract", "command": "contract", "relevance": 0.9},
        ], [], client)
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(result.ordered_ids, ("second", "first"))

    def test_invalid_answer_type_unknown_id_and_extra_answer_fail_closed(self):
        invalid_answers = [
            {"first": {"type": "rank", "choice": "t1", "confidence": 0.99}},
            {"first": {"type": "choice", "choice": "unknown", "confidence": 0.99}},
            {"first": {"type": "choice", "choice": "t1", "confidence": 0.99},
             "extra": {"type": "choice", "choice": "t2", "confidence": 0.99}},
        ]
        for answer in invalid_answers:
            with self.subTest(answer=answer):
                client = FakeClient(answer)
                result = rank_tests("python", [
                    TestCandidate(TestKind.UNIT, "private-unit"),
                    TestCandidate(TestKind.INTEGRATION, "private-integration"),
                ], [], client)
                self.assertEqual(result.status, NO_REMOTE_CHOICE)
                self.assertEqual(result.ordered_ids, ("t1", "t2"))

    def test_timeout_returns_safe_fallback_without_error_details(self):
        client = FakeClient(error=TimeoutError("private path /client/secret"))
        result = rank_tests("frontend", [
            TestCandidate(TestKind.E2E, "npm run private-e2e"),
            TestCandidate(TestKind.SMOKE, "npm run smoke-private"),
        ], [], client)
        self.assertEqual(result.status, NO_REMOTE_CHOICE)
        self.assertEqual(result.ordered_ids, ("t1", "t2"))
        self.assertNotIn("secret", repr(result))

    def test_non_finite_or_boolean_confidence_is_rejected(self):
        for confidence in (True, float("nan"), float("inf"), "uncertain"):
            with self.subTest(confidence=confidence):
                client = FakeClient({
                    "first": {"type": "choice", "choice": "t1",
                              "confidence": confidence},
                })
                result = rank_tests("config", [
                    TestCandidate(TestKind.LINT, "lint"),
                    TestCandidate(TestKind.TYPECHECK, "typecheck"),
                ], [], client)
                self.assertEqual(result.status, NO_REMOTE_CHOICE)


if __name__ == "__main__":
    unittest.main()
