from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pilot_contract_triage_pair as runner


def command_event(kind, event_id, argv, **extra):
    return json.dumps({
        "type": kind,
        "item": {
            "id": event_id,
            "type": "command_execution",
            "command": shlex.join(argv),
            **extra,
        },
    })


def assistant_event(text):
    return json.dumps({
        "type": "item.completed",
        "item": {"id": "assistant", "type": "agent_message", "text": text},
    })


class InitialFailureTriageStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        fixture = root / "fixture"
        fixture.mkdir()
        oracle = root / "oracle.py"
        oracle.write_text("pass\n", encoding="utf-8")
        self.profile = runner.CaseProfile(
            case_id="stage-repair",
            fixture_source=fixture,
            task_prompt="Repair the behavior in src/service.py.",
            source_file="src/service.py",
            focused_test_file="tests/test_service.py",
            focused_command=("python", "-m", "unittest", "tests.test_service", "-q"),
            evidence_files={
                "contract": "contract.md",
                "implementation": "src/service.py",
                "test": "tests/test_service.py",
            },
            evidence_markers={
                "contract": ("contract marker",),
                "implementation": ("implementation marker",),
                "test": ("test marker",),
            },
            failure_markers=("AssertionError: mismatch",),
            triage_kinds=("assertion",),
            triage_hypotheses=(
                "assertion_behavior_regression",
                "assertion_expectation_drift",
            ),
            triage_accepted_ids=("confirm_behavior_contract",),
            triage_accepted_statuses=("no-remote-choice",),
            triage_observations={"assertion": ("contract_underspecified",)},
            rank_hypotheses=False,
            outcome_mode="repair",
            oracle_script=oracle,
            oracle_sha256=hashlib.sha256(oracle.read_bytes()).hexdigest(),
        )

    def _triage_output(self):
        return json.dumps({
            "observed_exit_status": 1,
            "test_failed": True,
            "status": "no-remote-choice",
            "steps": [{"id": "confirm_behavior_contract"}],
            "executed": False,
            "decision_usage": None,
        })

    def _stream(self, *, stage="initial-failure", failure=True,
                bad_command=False, bad_discriminator=False, unknown_output=False,
                late_ack=False):
        profile = self.profile
        lines = [assistant_event(runner.WORKFLOW_ACK_LINE)]
        focused = list(profile.focused_command)
        if failure:
            lines.extend([
                command_event("item.started", "focused", focused),
                command_event(
                    "item.completed", "focused", focused, exit_code=1,
                    aggregated_output="AssertionError: mismatch",
                ),
            ])
        else:
            lines.append(command_event(
                "item.started", "unfailed-tool", ["python", "-c", "print('probe')"],
            ))
        contract = ["cat", "contract.md"]
        lines.extend([
            command_event("item.started", "contract", contract),
            command_event(
                "item.completed", "contract", contract, exit_code=0,
                aggregated_output=("unhelpful local output" if bad_discriminator else "contract marker"),
            ),
        ])
        argv = runner._triage_argv(
            1, profile, include_observations=stage != "initial-failure",
        )
        if bad_command:
            argv = [*argv, "--hypothesis", "unknown_hypothesis"]
        lines.extend([
            command_event("item.started", "triage", argv),
            command_event(
                "item.completed", "triage", argv, exit_code=0,
                aggregated_output=(
                    json.dumps({
                        "observed_exit_status": 1,
                        "test_failed": True,
                        "status": "no-remote-choice",
                        "steps": [{"id": "unknown_step_id"}],
                        "executed": False,
                    }) if unknown_output else self._triage_output()
                ),
            ),
        ])
        if not late_ack:
            lines.append(assistant_event(runner.PROFILE_TRIAGE_RESULT_ACK_LINE))
        for kind in ("implementation", "test"):
            argv = ["cat", profile.evidence_files[kind]]
            lines.extend([
                command_event("item.started", kind, argv),
                command_event(
                    "item.completed", kind, argv, exit_code=0,
                    aggregated_output=profile.evidence_markers[kind][0],
                ),
            ])
        if late_ack:
            lines.append(assistant_event(runner.PROFILE_TRIAGE_RESULT_ACK_LINE))
        return lines

    def _score(self, **kwargs):
        lines = self._stream(**kwargs)
        return runner._event_receipts(
            lines, [float(index + 1) for index in range(len(lines))], 0.0,
            advice_policy="nonbinding", profile=self.profile,
            triage_stage=kwargs.get("stage", "initial-failure"),
        )[0]

    def test_initial_failure_stage_allows_advice_after_one_verified_discriminator(self):
        result = self._score()
        self.assertEqual(result["triage_output_status"], "valid_configured_choice")
        self.assertTrue(result["triage_stage_eligible_before_request"])
        self.assertTrue(result["triage_after_local_discriminator"])
        self.assertTrue(result["triage_before_evidence_complete"])
        self.assertFalse(result["triage_after_evidence"])
        self.assertFalse(result["evidence_complete_before_triage"])
        self.assertEqual(
            result["evidence_categories_verified"],
            ["contract", "implementation", "test"],
        )
        self.assertEqual(result["triage_result_acknowledgment"], "before_next_tool")

    def test_default_evidence_reviewed_stage_rejects_same_early_request(self):
        result = self._score(stage="evidence-reviewed")
        self.assertEqual(result["triage_output_status"], "out_of_order_or_unverified")
        self.assertTrue(result["triage_invalid_invocation_observed"])
        self.assertFalse(result["triage_stage_eligible_before_request"])
        self.assertFalse(result["triage_after_evidence"])

    def test_no_observed_failure_or_unknown_hypothesis_never_scores_valid(self):
        no_failure = self._score(failure=False)
        self.assertEqual(no_failure["initial_focused_exit"], None)
        self.assertEqual(no_failure["triage_output_status"], "out_of_order_or_unverified")
        self.assertTrue(no_failure["triage_invalid_invocation_observed"])
        self.assertFalse(no_failure["triage_stage_eligible_before_request"])

        unknown = self._score(bad_command=True)
        self.assertEqual(unknown["triage_output_status"], "out_of_order_or_unverified")
        self.assertTrue(unknown["triage_invalid_invocation_observed"])
        self.assertFalse(unknown["triage_after_local_discriminator"])

    def test_missing_local_discriminator_and_unknown_result_id_are_rejected(self):
        no_discriminator = self._score(bad_discriminator=True)
        self.assertEqual(
            no_discriminator["triage_output_status"],
            "out_of_order_or_unverified",
        )
        self.assertFalse(no_discriminator["triage_stage_eligible_before_request"])
        self.assertTrue(no_discriminator["triage_invalid_invocation_observed"])

        unknown_result = self._score(unknown_output=True)
        self.assertEqual(unknown_result["triage_output_status"], "invalid_output")
        self.assertTrue(unknown_result["triage_stage_eligible_before_request"])
        self.assertTrue(unknown_result["triage_after_local_discriminator"])

    def test_late_result_ack_does_not_satisfy_delivery_ack_gate(self):
        result = self._score(late_ack=True)
        self.assertEqual(result["triage_output_status"], "valid_configured_choice")
        self.assertEqual(result["triage_result_acknowledgment"], "after_next_tool")
        self.assertNotEqual(result["triage_result_acknowledgment"], "before_next_tool")

    def test_prompt_and_bridge_omit_unverified_observations_but_keep_repair_gates(self):
        prompt = runner._case_treatment_prompt(
            self.profile, "nonbinding", "initial-failure",
        )
        self.assertIn("one relevant configured evidence item as a cheap local discriminator", prompt)
        self.assertIn("at least two configured causal hypotheses still fit", prompt)
        self.assertIn("before reviewing the remaining evidence", prompt)
        self.assertIn("inspect all configured evidence before completing the repair", prompt)
        self.assertIn("git diff --check", prompt)
        self.assertIn("focused test again", prompt)
        self.assertIn("full test suite", prompt)
        self.assertIn("separate immutable oracle", prompt)
        initial_argv = runner._triage_argv(
            1, self.profile, include_observations=False,
        )
        self.assertNotIn("--assertion-observation", initial_argv)
        self.assertIn("--assertion-observation", runner._triage_argv(1, self.profile))
        spec = runner._profile_bridge_spec(self.profile, include_observations=False)
        self.assertEqual(spec.allowed_observations, {})

    def test_initial_failure_stage_is_restricted_to_nonbinding_repair_profiles(self):
        with self.assertRaisesRegex(ValueError, "nonbinding repair case profile"):
            runner.run_pair(
                codex="unused", model="test-model", reasoning_effort="low",
                output_dir=Path(self.temp.name) / "not-allowed",
                case_profile=self.profile, advice_policy="legacy-required-step",
                triage_stage="initial-failure",
            )
        triage_profile = __import__("dataclasses").replace(
            self.profile, outcome_mode="contract_triage",
        )
        with self.assertRaisesRegex(ValueError, "nonbinding repair case profile"):
            runner.run_pair(
                codex="unused", model="test-model", reasoning_effort="low",
                output_dir=Path(self.temp.name) / "not-repair",
                case_profile=triage_profile, advice_policy="nonbinding",
                triage_stage="initial-failure",
            )

    def test_run_pair_routes_stage_to_treatment_without_bypassing_repair_failure(self):
        fixture = self.profile.fixture_source
        (fixture / "src").mkdir()
        (fixture / "tests").mkdir()
        (fixture / "src/service.py").write_text("VALUE = 0\\n", encoding="utf-8")
        (fixture / "tests/test_service.py").write_text("def test_service(): pass\\n", encoding="utf-8")
        (fixture / "contract.md").write_text("contract marker\\n", encoding="utf-8")
        calls = []

        def fake_arm(**kwargs):
            calls.append(kwargs)
            return ({
                "cli_status": "completed",
                "completion_ms": 10.0,
                "initial_focused_exit": 1,
                "focused_exit_codes": [1, 1],
                "first_useful_failure_observed": True,
                "full_suite_invocation_observed": True,
                "full_suite_exit": 1,
                "evidence_categories_verified": sorted(self.profile.evidence_files),
                "triage_invocation_observed": kwargs["treatment"],
                "triage_after_evidence": False,
                "triage_after_local_discriminator": kwargs["treatment"],
                "triage_stage_eligible_before_request": kwargs["treatment"],
                "triage_invalid_invocation_observed": False,
                "triage_exit_code": 0 if kwargs["treatment"] else None,
                "triage_output_status": (
                    "valid_configured_choice" if kwargs["treatment"] else "not_invoked"
                ),
                "workflow_acknowledgment": "before_first_tool",
                "triage_result_acknowledgment": "before_next_tool",
                "task_outcome_status": "incomplete",
                "agent_git_diff_check_invocation_observed": True,
                "agent_git_diff_check_exit_codes": [0],
                "agent_git_diff_check_exit_code": 0,
                "agent_git_diff_check_passed": True,
            }, None)

        with (
            mock.patch.object(runner, "_verify_codex_version", return_value=True),
            mock.patch.object(runner.core, "_copy_auth", return_value=True),
            mock.patch.object(runner, "_run_arm", side_effect=fake_arm),
            mock.patch.object(runner, "_run_independent_oracle", return_value={
                "status": "passed", "exit_code": 0, "elapsed_ms": 1.0,
                "oracle_sha256": self.profile.oracle_sha256, "oracle_unchanged": True,
            }),
        ):
            receipt = runner.run_pair(
                codex="unused", model="test-model", reasoning_effort="low",
                output_dir=Path(self.temp.name) / "pair-run",
                case_profile=self.profile,
                advice_policy="nonbinding",
                triage_stage="initial-failure",
            )

        self.assertEqual(len(calls), 2, receipt)
        treatment_call = next(call for call in calls if call["treatment"])
        baseline_call = next(call for call in calls if not call["treatment"])
        self.assertEqual(treatment_call["triage_stage"], "initial-failure")
        self.assertNotIn("triage_stage", baseline_call)
        self.assertEqual(receipt["triage_stage"], "initial-failure")
        self.assertEqual(receipt["protocol_delivery_status"], "advice_delivered")
        self.assertEqual(receipt["task_outcome_acceptance_status"], "failed")
        self.assertEqual(receipt["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
