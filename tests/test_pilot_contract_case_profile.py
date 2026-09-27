"""Offline acceptance tests for case-profile repair runs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_contract_triage_pair.py"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("pilot_contract_triage_pair_case_profile_tests", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class PilotContractCaseProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".pilot-case-profile-test-", dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = self.root / "fixture"
        (self.fixture / "src").mkdir(parents=True)
        (self.fixture / "tests").mkdir()
        (self.fixture / "src" / "service.py").write_text("VALUE = 0\n", encoding="utf-8")
        (self.fixture / "tests" / "test_service.py").write_text(
            "def test_value():\n    assert True\n", encoding="utf-8"
        )
        (self.fixture / "TASK.md").write_text(
            "Repair service.py so the frozen expected value is returned.\n", encoding="utf-8"
        )
        (self.fixture / "contract.md").write_text(
            "The expected value is one.\n", encoding="utf-8"
        )

        self.oracle = self.root / "supervisor_oracle.py"
        self.oracle.write_text(
            "import argparse\nfrom pathlib import Path\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--fixture-dir', required=True)\n"
            "root = Path(p.parse_args().fixture_dir)\n"
            "raise SystemExit(0 if (root / 'src/service.py').read_text() == 'VALUE = 1\\n' else 9)\n",
            encoding="utf-8",
        )
        self.profile_data = {
            "schema_version": 1,
            "case_id": "synthetic-repair",
            "fixture_source": self.fixture.relative_to(ROOT).as_posix(),
            "task_prompt_file": "TASK.md",
            "source_file": "src/service.py",
            "focused_test_file": "tests/test_service.py",
            "focused_command": ["python", "-m", "unittest", "tests.test_service", "-q"],
            "evidence_files": {"contract": "contract.md"},
            "evidence_markers": {"contract": ["expected value", "one"]},
            "failure_markers": ["AssertionError"],
            "triage": {
                "kinds": ["assertion"],
                "hypotheses": [
                    "assertion_behavior_regression",
                    "assertion_expectation_drift",
                ],
                "accepted_ids": ["assertion_behavior_regression"],
                "accepted_statuses": ["no-remote-choice"],
                "observations": {},
                "rank_hypotheses": True,
            },
            "outcome_mode": "repair",
            "oracle_script": self.oracle.relative_to(ROOT).as_posix(),
            "oracle_sha256": hashlib.sha256(self.oracle.read_bytes()).hexdigest(),
        }
        self.profile_path = self.root / "profile.json"
        self.write_profile()
        self.profile = runner.load_case_profile(self.profile_path)

    def write_profile(self, value=None):
        self.profile_path.write_text(
            json.dumps(self.profile_data if value is None else value),
            encoding="utf-8",
        )

    def run_mocked_pair(
        self, mode="success", oracle_expects_solution=True,
        advice_policy="legacy-required-step",
    ):
        profile = self.profile
        if not oracle_expects_solution:
            self.oracle.write_text(
                "import argparse\nfrom pathlib import Path\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--fixture-dir', required=True)\n"
                "root = Path(p.parse_args().fixture_dir)\n"
                "raise SystemExit(0 if (root / 'src/service.py').read_text() == 'VALUE = 2\\n' else 9)\n",
                encoding="utf-8",
            )
            self.profile_data["oracle_sha256"] = hashlib.sha256(
                self.oracle.read_bytes()
            ).hexdigest()
            self.write_profile()
            profile = runner.load_case_profile(self.profile_path)

        def copy_auth(_src, dest):
            dest = Path(dest)
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            dest.write_text("{}", encoding="utf-8")
            return True

        def fake_arm(**kwargs):
            fixture = kwargs["fixture"]
            measurement_path = kwargs["measurement_path"]
            measurement = {
                "event_count": 3 if mode == "timeout" and kwargs["treatment"] else 6,
                "cli_exit_code": None if mode == "timeout" and kwargs["treatment"] else 0,
                "failure": "timeout" if mode == "timeout" and kwargs["treatment"] else None,
                "token_total": None,
            }
            runner.common._private_write(
                measurement_path,
                (json.dumps(measurement, sort_keys=True) + "\n").encode("utf-8"),
            )
            if mode == "timeout" and kwargs["treatment"]:
                return {
                    "cli_status": "timeout",
                    "failure": "timeout",
                    "cli_exit_code": None,
                    "completion_ms": None,
                    "agent_measurement": measurement,
                    "event_count": measurement["event_count"],
                    "initial_focused_exit": 1,
                    "focused_exit_codes": [1],
                    "first_useful_failure_observed": True,
                    "full_suite_invocation_observed": False,
                    "full_suite_exit": None,
                }, None

            repair_succeeds = not (
                mode == "bad-repair-with-advice" and kwargs["treatment"]
            )
            if repair_succeeds:
                (fixture / profile.source_file).write_text("VALUE = 1\n", encoding="utf-8")
            if mode == "mutated-tests":
                (fixture / profile.focused_test_file).write_text(
                    "def test_value():\n    assert False\n", encoding="utf-8"
                )
            result = {
                "cli_status": "completed",
                "failure": None,
                "cli_exit_code": 0,
                "completion_ms": 12.5,
                "agent_measurement": measurement,
                "event_count": measurement["event_count"],
                "initial_focused_exit": 1,
                "focused_exit_codes": [1, 0] if repair_succeeds else [1, 1],
                "first_useful_failure_observed": True,
                "full_suite_invocation_observed": True,
                "full_suite_exit": 0 if repair_succeeds else 1,
            }
            if advice_policy == "nonbinding" and kwargs["treatment"]:
                result.update({
                    "workflow_acknowledgment": (
                        None if mode == "missing-ack" else "before_first_tool"
                    ),
                    "evidence_categories_verified": list(profile.evidence_files),
                    "triage_after_evidence": True,
                    "triage_invalid_invocation_observed": False,
                    "triage_exit_code": 0,
                    "triage_output_status": "valid_configured_choice",
                    "triage_result_acknowledgment": "before_next_tool",
                    "triage_invocation_observed": True,
                })
            return result, "repaired source" if repair_succeeds else "triage but repair failed"

        output = self.root / f"receipt-{mode}-{oracle_expects_solution}-{advice_policy}"
        with (
            mock.patch.object(runner, "_verify_codex_version", return_value=True),
            mock.patch.object(runner.core, "_copy_auth", side_effect=copy_auth),
            mock.patch.object(runner, "_run_arm", side_effect=fake_arm),
        ):
            receipt = runner.run_pair(
                codex="mock-codex",
                model="mock-model",
                reasoning_effort="medium",
                timeout=10,
                seed=4,
                output_dir=output,
                case_profile=profile,
                advice_policy=advice_policy,
            )
        return receipt, output

    def test_valid_profile_parses_allowlisted_paths_and_enums(self):
        self.assertEqual(self.profile.case_id, "synthetic-repair")
        self.assertEqual(self.profile.outcome_mode, "repair")
        self.assertTrue(self.profile.rank_hypotheses)
        self.assertEqual(self.profile.triage_kinds, ("assertion",))
        self.assertEqual(self.profile.triage_hypotheses, (
            "assertion_behavior_regression",
            "assertion_expectation_drift",
        ))
        self.assertEqual(self.profile.fixture_source, self.fixture)

    def test_profile_rejects_escaping_path_and_unknown_enum(self):
        escaping = dict(self.profile_data)
        escaping["source_file"] = "../outside.py"
        self.write_profile(escaping)
        with self.assertRaisesRegex(ValueError, "case-profile path"):
            runner.load_case_profile(self.profile_path)

        invalid_enum = json.loads(json.dumps(self.profile_data))
        invalid_enum["triage"]["kinds"] = ["shell"]
        self.write_profile(invalid_enum)
        with self.assertRaisesRegex(ValueError, "case-profile kind"):
            runner.load_case_profile(self.profile_path)

    def test_both_arms_must_meet_the_same_repair_and_oracle_gates(self):
        receipt, _output = self.run_mocked_pair()
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["task_outcome_acceptance_status"], "passed")
        self.assertEqual(receipt["advice_adoption_status"], "not_measured")
        self.assertEqual(set(receipt["arms"]), {"arm-a", "arm-b"})
        for arm in receipt["arms"].values():
            self.assertEqual(arm["task_outcome_status"], "completed")
            self.assertEqual(arm["independent_oracle"]["status"], "passed")
            self.assertEqual(arm["independent_oracle"]["oracle_unchanged"], True)
            self.assertIsNotNone(arm["validated_completion_ms"])
        self.assertEqual(set(receipt["source_changed"].values()), {True})
        self.assertEqual(set(receipt["tests_unchanged"].values()), {True})

    def test_wrong_oracle_expectation_prevents_repair_acceptance(self):
        receipt, _output = self.run_mocked_pair(oracle_expects_solution=False)
        self.assertEqual(receipt["status"], "incomplete")
        self.assertEqual(receipt["task_outcome_acceptance_status"], "failed")
        for arm in receipt["arms"].values():
            self.assertEqual(arm["independent_oracle"]["status"], "failed")
            self.assertEqual(arm["task_outcome_status"], "incomplete")
            self.assertIsNone(arm["validated_completion_ms"])

    def test_mutated_tests_prevent_acceptance_even_if_oracle_passes(self):
        receipt, _output = self.run_mocked_pair(mode="mutated-tests")
        self.assertEqual(receipt["status"], "incomplete")
        self.assertEqual(set(receipt["tests_unchanged"].values()), {False})
        for arm in receipt["arms"].values():
            self.assertEqual(arm["independent_oracle"]["status"], "passed")
            self.assertEqual(arm["task_outcome_status"], "incomplete")
            self.assertIsNone(arm["validated_completion_ms"])

    def test_successful_repair_can_pass_task_gate_when_workflow_ack_is_missing(self):
        receipt, _output = self.run_mocked_pair(
            mode="missing-ack",
            advice_policy="nonbinding",
        )
        self.assertEqual(receipt["task_outcome_acceptance_status"], "passed")
        self.assertEqual(receipt["task_correctness_status"], "passed")
        self.assertEqual(receipt["protocol_delivery_status"], "delivery_failed")
        self.assertEqual(receipt["status"], "incomplete")
        treatment = next(
            arm for arm in receipt["arms"].values()
            if "workflow_acknowledgment" in arm
        )
        self.assertEqual(treatment["independent_oracle"]["status"], "passed")

    def test_acknowledged_triage_does_not_make_invalid_repair_pass(self):
        receipt, _output = self.run_mocked_pair(
            mode="bad-repair-with-advice",
            advice_policy="nonbinding",
        )
        self.assertEqual(receipt["task_outcome_acceptance_status"], "failed")
        self.assertEqual(receipt["task_correctness_status"], "failed")
        self.assertEqual(receipt["protocol_delivery_status"], "advice_delivered")
        self.assertEqual(receipt["advice_adoption_status"], "not_measured")
        treatment = next(
            arm for arm in receipt["arms"].values()
            if arm.get("triage_output_status") == "valid_configured_choice"
        )
        self.assertEqual(treatment["triage_output_status"], "valid_configured_choice")
        self.assertEqual(treatment["task_outcome_status"], "incomplete")
        self.assertEqual(treatment["independent_oracle"]["status"], "failed")

    def test_treatment_timeout_fails_with_partial_measurement_retained(self):
        receipt, output = self.run_mocked_pair(mode="timeout")
        self.assertEqual(receipt["status"], "incomplete")
        self.assertEqual(receipt["task_outcome_acceptance_status"], "failed")
        timed_out = next(
            arm for arm in receipt["arms"].values() if arm["cli_status"] == "timeout"
        )
        self.assertEqual(timed_out["event_count"], 3)
        self.assertEqual(timed_out["agent_measurement"]["event_count"], 3)
        label = next(
            label for label, arm in receipt["arms"].items() if arm["cli_status"] == "timeout"
        )
        self.assertTrue(receipt["blind_artifacts"][label]["agent_measurement_captured"])
        self.assertTrue((output / f"{label}-agent-measurement.json").is_file())


if __name__ == "__main__":
    unittest.main()
