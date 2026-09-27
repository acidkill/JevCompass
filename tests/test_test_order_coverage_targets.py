"""Offline tests for candidate-level coverage target metadata."""

from __future__ import annotations

import json
import unittest

from jevcompass.test_order import (
    ChangeSignal,
    DecisionReason,
    NO_REMOTE_CHOICE,
    REMOTE_CHOICE,
    RequiredTest,
    TestCandidate,
    TestKind,
    rank_tests,
)


ALLOWED_TARGETS = {
    "public_contract_changed",
    "boundary_mapping_changed",
    "internal_logic_changed",
}


class FakeClient:
    def __init__(self, answer=None):
        self.answer = answer or {
            "first": {"type": "choice", "choice": "t2", "confidence": 0.9},
        }
        self.calls = []

    def decide(self, state, questions):
        self.calls.append((state, questions))
        return self.answer


def candidates(*, targets=()):
    return [
        {
            "id": "private-unit-id",
            "kind": "unit",
            "command": "pytest /secret/client/test_unit.py",
            "relevance": 0.5,
            "coverage_targets": targets,
        },
        {
            "id": "private-contract-id",
            "kind": "contract",
            "command": "pytest /secret/client/test_contract.py",
            "relevance": 0.5,
            "coverage_targets": ("internal_logic_changed",),
        },
    ]


class TestOrderCoverageTargetTests(unittest.TestCase):
    def test_existing_construction_defaults_to_empty_and_old_request_shape(self):
        candidate = TestCandidate(TestKind.UNIT, "private test command")
        self.assertEqual(candidate.coverage_targets, ())

        client = FakeClient()
        result = rank_tests("api", [
            {"kind": "unit", "command": "private unit", "relevance": 0.5},
            {"kind": "contract", "command": "private contract", "relevance": 0.5},
        ], [], client)

        self.assertEqual(result.status, REMOTE_CHOICE)
        state, _ = client.calls[0]
        self.assertTrue(all("coverage_targets" not in item for item in state["candidates"]))

    def test_targets_are_enum_normalized_deduplicated_and_sent_as_safe_fields(self):
        client = FakeClient()
        result = rank_tests("api", candidates(targets=(
            " PUBLIC_CONTRACT_CHANGED ",
            ChangeSignal.PUBLIC_CONTRACT_CHANGED,
            "boundary_mapping_changed",
        )), [], client)

        self.assertEqual(result.status, REMOTE_CHOICE)
        state, _ = client.calls[0]
        safe_state = json.dumps(state, sort_keys=True)
        self.assertEqual(
            state["candidates"][0]["coverage_targets"],
            ["boundary_mapping_changed", "public_contract_changed"],
        )
        self.assertEqual(
            state["candidates"][1]["coverage_targets"],
            ["internal_logic_changed"],
        )
        self.assertTrue(
            {target for item in state["candidates"]
             for target in item["coverage_targets"]} <= ALLOWED_TARGETS
        )
        for private_value in (
            "private-unit-id", "private-contract-id", "/secret/client",
            "private test command",
        ):
            self.assertNotIn(private_value, safe_state)

    def test_unknown_or_malformed_targets_abstain_without_forwarding_values(self):
        bad_target_inputs = (
            ("/private/path/secret",),
            "public_contract_changed",
            None,
            (["nested", "private"],),
        )
        for bad_targets in bad_target_inputs:
            with self.subTest(bad_targets=bad_targets):
                client = FakeClient()
                supplied = candidates(targets=bad_targets)
                required = [RequiredTest("pytest required", "gate")]

                result = rank_tests("api", supplied, required, client)

                self.assertEqual(result.status, NO_REMOTE_CHOICE)
                self.assertIsNone(result.decision_reason)
                self.assertEqual(result.required, tuple(required))
                self.assertEqual(client.calls, [])
                serialized = json.dumps(
                    [candidate.coverage_targets for candidate in result.ordered_candidates]
                )
                self.assertNotIn("private", serialized)
                self.assertNotIn("secret", serialized)

    def test_valid_targets_do_not_force_remote_choice_or_change_local_resolution(self):
        client = FakeClient()
        local = rank_tests("api", [{
            "kind": "unit",
            "command": "pytest",
            "coverage_targets": ("public_contract_changed",),
        }], [], client)
        self.assertEqual(local.status, NO_REMOTE_CHOICE)
        self.assertEqual(local.decision_reason, DecisionReason.NO_CHOICE_NEEDED)
        self.assertEqual(client.calls, [])

        locally_resolved = rank_tests("api", [
            {
                "kind": "unit",
                "command": "direct fast",
                "coverage": "direct",
                "runtime": "fast",
                "coverage_targets": ("boundary_mapping_changed",),
            },
            {
                "kind": "integration",
                "command": "indirect slow",
                "coverage": "indirect",
                "runtime": "slow",
            },
        ], [], client, signals=("boundary_mapping_changed",))
        self.assertEqual(locally_resolved.status, NO_REMOTE_CHOICE)
        self.assertEqual(locally_resolved.ordered_candidates[0].kind, TestKind.UNIT)
        self.assertEqual(locally_resolved.decision_reason, DecisionReason.LOCAL_RESOLUTION)
        self.assertEqual(client.calls, [])

    def test_valid_targets_preserve_acceptance_fallback_and_required_checks(self):
        required = [RequiredTest("pytest full suite", "full")]
        accepted_client = FakeClient()
        accepted = rank_tests(
            "api",
            candidates(targets=("public_contract_changed",)),
            required,
            accepted_client,
        )
        self.assertEqual(accepted.status, REMOTE_CHOICE)
        self.assertEqual(accepted.decision_reason, DecisionReason.ACCEPTED)
        self.assertEqual(accepted.ordered_ids[0], "private-contract-id")
        self.assertEqual(accepted.required, tuple(required))

        fallback_client = FakeClient({
            "first": {"type": "choice", "choice": "t2", "confidence": 0.69},
        })
        fallback = rank_tests(
            "api",
            candidates(targets=("public_contract_changed",)),
            required,
            fallback_client,
        )
        self.assertEqual(fallback.status, NO_REMOTE_CHOICE)
        self.assertEqual(
            fallback.decision_reason, DecisionReason.INSUFFICIENT_CONFIDENCE
        )
        self.assertEqual(fallback.ordered_ids, ("private-unit-id", "private-contract-id"))
        self.assertEqual(fallback.required, tuple(required))


class CoverageTargetsCliTests(unittest.TestCase):
    def test_cli_retains_normalized_targets_but_omits_unspecified_field(self):
        import contextlib
        import io
        from jevcompass.cli import _display_test_order
        metadata = candidates(targets=["internal_logic_changed", "internal_logic_changed"])
        metadata[1].pop("coverage_targets", None)
        result = rank_tests("mixed", metadata,
                            [RequiredTest("python -m unittest", "ci")], client=FakeClient())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            _display_test_order(result, True)
        payload = json.loads(output.getvalue())
        supplied = next(item for item in payload["ordered"] if item["id"] == "private-unit-id")
        self.assertEqual(supplied["coverage_targets"], ["internal_logic_changed"])
        self.assertFalse(payload["executed"])
        for item in payload["ordered"]:
            if item["id"] != "private-unit-id":
                self.assertNotIn("coverage_targets", item)
        self.assertEqual(payload["required"], [{"id": "ci", "command": "python -m unittest"}])


if __name__ == "__main__":
    unittest.main()
