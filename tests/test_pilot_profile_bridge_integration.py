from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]

import pilot_contract_triage_pair as runner
import pilot_profile_triage_bridge as bridge_module


def _item_event(kind: str, event_id: str, command: list[str], **extra: object) -> str:
    return json.dumps({
        "type": kind,
        "item": {
            "id": event_id,
            "type": "command_execution",
            "command": shlex.join(command),
            **extra,
        },
    })


class ProfileBridgeArmIntegrationTests(unittest.TestCase):
    def _profile(self, root: Path) -> runner.CaseProfile:
        fixture = root / "fixture"
        (fixture / "tests").mkdir(parents=True)
        (fixture / "src").mkdir()
        (fixture / "README.md").write_text("contract marker\n", encoding="utf-8")
        (fixture / "src/service.py").write_text("VALUE = 0\n", encoding="utf-8")
        (fixture / "tests/test_service.py").write_text("assert True\n", encoding="utf-8")
        oracle = root / "oracle.py"
        oracle.write_text("pass\n", encoding="utf-8")
        return runner.CaseProfile(
            case_id="bridge-integration",
            fixture_source=fixture,
            task_prompt="Inspect and classify the synthetic failing test.",
            source_file="src/service.py",
            focused_test_file="tests/test_service.py",
            focused_command=("python", "-m", "unittest", "tests.test_service"),
            evidence_files={"contract": "README.md"},
            evidence_markers={"contract": ("contract marker",)},
            failure_markers=("AssertionError: synthetic mismatch",),
            triage_kinds=("assertion",),
            triage_hypotheses=(
                "assertion_behavior_regression",
                "assertion_expectation_drift",
            ),
            triage_accepted_ids=("confirm_behavior_contract",),
            triage_accepted_statuses=("no-remote-choice",),
            triage_observations={},
            rank_hypotheses=False,
            outcome_mode="contract_triage",
            oracle_script=oracle,
            oracle_sha256=hashlib.sha256(oracle.read_bytes()).hexdigest(),
        )

    def _fake_decision(self, _args, observed_exit):
        identifier = "confirm_behavior_contract"
        usage = {"input_tokens": 11, "output_tokens": 4}
        step = {
            "id": identifier,
            "title": "Confirm behavior contract",
            "instruction": "Confirm the expected behavior with the owner.",
            "selection_source": "remote_preferred_next_step",
        }
        receipt = {
            "schema_version": 1,
            "status": "remote-choice",
            "bridge_request_count": 1,
            "observed_exit_status": observed_exit,
            "test_failed": True,
            "executed": False,
            "decision_reason": "accepted",
            "diagnostic_step_ids": [identifier],
            "diagnostic_choice_id": identifier,
            "diagnostic_selection_source": "remote_preferred_next_step",
            "hypothesis_order": [],
            "hypothesis_ranking_status": "not_established",
            "cache_hit": False,
            "provider_transport_call_count": 1,
            "decision_usage_status": "reported",
            "decision_usage": usage,
        }
        payload = {
            "status": "remote-choice",
            "observed_exit_status": observed_exit,
            "test_failed": True,
            "executed": False,
            "decision_reason": "accepted",
            "cache_hit": False,
            "hypothesis_ranking_status": "not_established",
            "steps": [step],
            "decision_usage": usage,
        }
        return payload, receipt

    def _fake_cli_source(self, profile: runner.CaseProfile, marker: Path, sleep_after: float = 0) -> str:
        triage = runner._triage_argv(1, profile)
        focus = list(profile.focused_command)
        evidence = ["cat", *profile.evidence_files.values()]
        return f"""
import json, os, subprocess, sys, time
def emit(kind, event_id, command, **extra):
    item = {{"id": event_id, "type": "command_execution",
            "command": __import__("shlex").join(command), **extra}}
    print(json.dumps({{"type": kind, "item": item}}), flush=True)
emit("item.started", "focus", {focus!r})
emit("item.completed", "focus", {focus!r}, exit_code=1,
     aggregated_output="AssertionError: synthetic mismatch")
emit("item.started", "evidence", {evidence!r})
emit("item.completed", "evidence", {evidence!r}, exit_code=0,
     aggregated_output="contract marker")
triage = {triage!r}
marker = {str(marker)!r}
deadline = time.monotonic() + 5
while not os.path.exists(marker) and time.monotonic() < deadline:
    time.sleep(0.005)
if not os.path.exists(marker):
    raise RuntimeError("collector did not acknowledge focused failure")
emit("item.started", "triage", triage)
completed = subprocess.run(triage, text=True, capture_output=True, check=False)
emit("item.completed", "triage", triage, exit_code=completed.returncode,
     aggregated_output=completed.stdout)
print(json.dumps({{"type": "item.completed", "item": {{
    "id": "final", "type": "agent_message", "text": "Done"
}}}}), flush=True)
time.sleep({sleep_after!r})
"""

    def _collect_with_ack(self, marker: Path):
        original = runner.core._collect_events

        def collect(*args, **kwargs):
            observer = kwargs.get("event_observer")

            def observing(event):
                if observer is not None:
                    observer(event)
                item = event.get("item", {})
                if (
                    event.get("type") == "item.completed"
                    and item.get("id") == "focus"
                ):
                    marker.write_text("observed", encoding="utf-8")

            kwargs["event_observer"] = observing
            return original(*args, **kwargs)

        return collect

    def test_timeout_preserves_measurement_and_typed_bridge_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = self._profile(root)
            output = root / "timeout"
            output.mkdir(mode=0o700)
            measurement = output / "agent-measurement.json"
            marker = measurement.with_suffix(".failure-observed")
            source = self._fake_cli_source(profile, marker, sleep_after=2)
            real_popen = subprocess.Popen

            with (
                mock.patch.object(
                    runner.core, "_collect_events",
                    side_effect=self._collect_with_ack(marker),
                ),
                mock.patch.object(
                    runner.common, "_cli_command",
                    return_value=[sys.executable, "-c", source],
                ),
                mock.patch.object(runner.subprocess, "Popen", wraps=real_popen),
                mock.patch.object(
                    bridge_module.ProfileTriageBridge, "_decide",
                    new=self._fake_decision,
                ),
            ):
                result, answer = runner._run_arm(
                    codex="fake", model="test-model", reasoning_effort="low",
                    prompt="synthetic", fixture=profile.fixture_source,
                    home=output / "home", timeout=1, treatment=True,
                    measurement_path=measurement, profile=profile,
                    profile_bridge_spec=runner._profile_bridge_spec(profile),
                    accepted_triage_statuses=("remote-choice",),
                    allow_network=True, max_tokens=300000,
                )

            self.assertIsNone(answer)
            self.assertEqual(result["failure"], "timeout")
            self.assertTrue(measurement.is_file())
            persisted_measurement = json.loads(measurement.read_text(encoding="utf-8"))
            self.assertEqual(persisted_measurement["status"], "failed")
            self.assertTrue(persisted_measurement["collector_failed"])
            typed_receipt = output / "agent-measurement-profile-triage.json"
            self.assertTrue(typed_receipt.is_file())
            persisted = json.loads(typed_receipt.read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], "remote-choice")
            self.assertEqual(result["profile_triage_typed_receipt"], persisted)
            self.assertEqual(result["provider_transport_call_count"], 1)

    def test_treatment_bridge_runs_after_observed_failure_and_receipt_survives_parser_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = self._profile(root)
            real_popen = subprocess.Popen
            spawn_envs = []
            network_flags = []
            cli_source = [""]

            def cli_command(_codex, _model, _effort, _prompt, *, allow_network=False):
                network_flags.append(allow_network)
                return [sys.executable, "-c", cli_source[0]]

            def popen(*args, **kwargs):
                spawn_envs.append(dict(kwargs["env"]))
                return real_popen(*args, **kwargs)

            def run_arm(label: str, parser_failure: bool):
                output = root / label
                output.mkdir(mode=0o700)
                measurement = output / "agent-measurement.json"
                marker = measurement.with_suffix(".failure-observed")
                cli_source[0] = self._fake_cli_source(profile, marker)
                with (
                    mock.patch.object(
                        runner.core, "_collect_events",
                        side_effect=self._collect_with_ack(marker),
                    ),
                    mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "parent-only-secret"}),
                    mock.patch.object(runner.common, "_cli_command", side_effect=cli_command),
                    mock.patch.object(runner.subprocess, "Popen", side_effect=popen),
                    mock.patch.object(
                        bridge_module.ProfileTriageBridge, "_decide",
                        new=self._fake_decision,
                    ),
                ):
                    if parser_failure:
                        with mock.patch.object(
                            runner, "_event_receipts",
                            side_effect=RuntimeError("synthetic parser failure"),
                        ):
                            return runner._run_arm(
                                codex="offline-fake-codex", model="test-model",
                                reasoning_effort="low", prompt="synthetic prompt",
                                fixture=profile.fixture_source, home=output / "home",
                                timeout=10, treatment=True, measurement_path=measurement,
                                profile=profile, profile_bridge_spec=runner._profile_bridge_spec(profile),
                                accepted_triage_statuses=("remote-choice",),
                                allow_network=True, max_tokens=300000,
                            )
                    return runner._run_arm(
                        codex="offline-fake-codex", model="test-model",
                        reasoning_effort="low", prompt="synthetic prompt",
                        fixture=profile.fixture_source, home=output / "home",
                        timeout=10, treatment=True, measurement_path=measurement,
                        profile=profile, profile_bridge_spec=runner._profile_bridge_spec(profile),
                        accepted_triage_statuses=("remote-choice",),
                        allow_network=True, max_tokens=300000,
                    )

            result, answer = run_arm("parsed", False)
            self.assertEqual(result["triage_output_status"], "valid_configured_choice")
            self.assertEqual(result["triage"]["status"], "remote-choice")
            self.assertEqual(result["provider_transport_call_count"], 1)
            self.assertEqual(result["diagnostic_choice_id"], "confirm_behavior_contract")
            self.assertEqual(result["decision_usage_status"], "reported")
            self.assertEqual(result["decision_usage"], {"input_tokens": 11, "output_tokens": 4})
            self.assertEqual(result["hypothesis_ranking_status"], "not_established")
            self.assertEqual(answer, "Done")
            self.assertTrue(result["profile_triage_bridge"]["observed_focused_failure"])
            self.assertEqual(result["profile_triage_bridge"]["state"], "completed")

            error_result, error_answer = run_arm("parser-error", True)
            self.assertEqual(error_result["failure"], "event_parser_error")
            self.assertIsNone(error_answer)
            self.assertTrue((root / "parser-error/agent-measurement.json").is_file())
            typed_receipt = root / "parser-error/agent-measurement-profile-triage.json"
            self.assertTrue(typed_receipt.is_file())
            persisted = json.loads(typed_receipt.read_text(encoding="utf-8"))
            self.assertEqual(persisted["status"], "remote-choice")
            self.assertEqual(error_result["profile_triage_typed_receipt"], persisted)
            self.assertEqual(error_result["provider_transport_call_count"], 1)

            self.assertEqual(network_flags, [True, True])
            self.assertNotIn("OPENROUTER_API_KEY", spawn_envs[-1])
            self.assertEqual(
                stat.S_IMODE((root / "parsed/agent-measurement-profile-triage.json").stat().st_mode),
                0o600,
            )
            self.assertEqual(
                json.loads((root / "parsed/agent-measurement.json").read_text())["status"],
                "completed",
            )

    def test_explicit_remote_mode_gives_equal_egress_but_treatment_only_bridge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = self._profile(root)
            output = root / "pair-output"
            received = []

            def fake_arm(**kwargs):
                received.append(kwargs)
                return ({
                    "cli_status": "completed",
                    "completion_ms": 5.0,
                    "initial_focused_exit": 1,
                    "focused_exit_codes": [1],
                    "first_useful_failure_observed": True,
                    "full_suite_invocation_observed": True,
                    "full_suite_exit": 1,
                    "triage_invocation_observed": kwargs["treatment"],
                    "triage_after_evidence": kwargs["treatment"],
                    "triage_invalid_invocation_observed": False,
                    "triage_exit_code": 0 if kwargs["treatment"] else None,
                    "triage_output_status": (
                        "valid_configured_choice" if kwargs["treatment"] else "not_invoked"
                    ),
                    "evidence_complete_before_triage": kwargs["treatment"],
                }, "answer")

            with (
                mock.patch.object(runner, "_verify_codex_version", return_value=True),
                mock.patch.object(runner.core, "_copy_auth", return_value=True),
                mock.patch.object(runner, "_run_arm", side_effect=fake_arm),
                mock.patch.object(runner, "_run_independent_oracle", return_value={
                    "status": "passed", "elapsed_ms": 1.0,
                }),
            ):
                receipt = runner.run_pair(
                    codex="fake", model="test-model", reasoning_effort="low",
                    timeout=5, seed=1, output_dir=output, case_profile=profile,
                    remote_profile_triage=True,
                    accepted_remote_statuses=("remote-choice",),
                    max_tokens=300000,
                )
                with self.assertRaisesRegex(ValueError, "explicit supervisor"):
                    runner.run_pair(
                        codex="fake", model="test-model", reasoning_effort="low",
                        timeout=5, seed=1, output_dir=root / "rejected",
                        case_profile=profile, remote_profile_triage=True,
                    )

            self.assertEqual(len(received), 2)
            self.assertEqual([kwargs["allow_network"] for kwargs in received], [True, True])
            self.assertEqual([kwargs["max_tokens"] for kwargs in received], [300000, 300000])
            treatment_call = next(kwargs for kwargs in received if kwargs["treatment"])
            baseline_call = next(kwargs for kwargs in received if not kwargs["treatment"])
            self.assertIsNotNone(treatment_call["profile_bridge_spec"])
            self.assertEqual(treatment_call["accepted_triage_statuses"], ("remote-choice",))
            self.assertIsNone(baseline_call["profile_bridge_spec"])
            self.assertIsNone(baseline_call["accepted_triage_statuses"])
            self.assertTrue(receipt["remote_profile_triage_enabled"])
            self.assertTrue(receipt["network_access_enabled_for_both_arms"])
            self.assertEqual(receipt["accepted_remote_statuses"], ["remote-choice"])
            self.assertEqual(receipt["observed_token_budget"], 300000)

    def test_explicit_local_abstention_status_remains_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = self._profile(root)
            profile = __import__("dataclasses").replace(
                profile,
                triage_accepted_ids=(
                    "assertion_behavior_regression",
                    "assertion_expectation_drift",
                ),
            )
            receipt_path = root / "abstention/agent-measurement-profile-triage.json"
            measurement_path = root / "abstention/agent-measurement.json"
            measurement_path.parent.mkdir(mode=0o700)
            marker = measurement_path.with_suffix(".failure-observed")
            source = self._fake_cli_source(profile, marker)
            real_popen = subprocess.Popen

            def local_decision(_bridge, _args, observed_exit):
                identifiers = [
                    "assertion_behavior_regression",
                    "assertion_expectation_drift",
                ]
                steps = [{
                    "id": identifier,
                    "title": identifier,
                    "instruction": "Compare the local contract and behavior.",
                    "selection_source": "unranked_local_fallback",
                } for identifier in identifiers]
                receipt = {
                    "schema_version": 1,
                    "status": "no-remote-choice",
                    "bridge_request_count": 1,
                    "observed_exit_status": observed_exit,
                    "test_failed": True,
                    "executed": False,
                    "decision_reason": "local_resolution",
                    "diagnostic_step_ids": identifiers,
                    "diagnostic_choice_id": identifiers[0],
                    "diagnostic_selection_source": "locally_resolved_guidance",
                    "hypothesis_order": [],
                    "hypothesis_ranking_status": "not_established",
                    "cache_hit": False,
                    "provider_transport_call_count": 0,
                    "decision_usage_status": "not_invoked",
                    "decision_usage": None,
                }
                return ({
                    "status": "no-remote-choice",
                    "observed_exit_status": observed_exit,
                    "test_failed": True,
                    "executed": False,
                    "decision_reason": "local_resolution",
                    "cache_hit": False,
                    "hypothesis_ranking_status": "not_established",
                    "steps": steps,
                    "decision_usage": None,
                }, receipt)

            with (
                mock.patch.object(
                    runner.core, "_collect_events",
                    side_effect=self._collect_with_ack(marker),
                ),
                mock.patch.object(
                    runner.common, "_cli_command",
                    return_value=[sys.executable, "-c", source],
                ),
                mock.patch.object(runner.subprocess, "Popen", wraps=real_popen),
                mock.patch.object(
                    bridge_module.ProfileTriageBridge, "_decide",
                    new=local_decision,
                ),
            ):
                result, _ = runner._run_arm(
                    codex="fake", model="test-model", reasoning_effort="low",
                    prompt="synthetic", fixture=profile.fixture_source,
                    home=root / "abstention/home", timeout=10, treatment=True,
                    measurement_path=measurement_path, profile=profile,
                    profile_bridge_spec=runner._profile_bridge_spec(profile),
                    accepted_triage_statuses=("no-remote-choice",),
                    allow_network=True,
                )

            self.assertEqual(result["triage"]["status"], "no-remote-choice")
            self.assertEqual(result["triage_output_status"], "valid_configured_choice", result)
            self.assertEqual(result["provider_transport_call_count"], 0)
            self.assertEqual(result["decision_usage_status"], "not_invoked")
            self.assertEqual(result["diagnostic_choice_id"], "assertion_behavior_regression")
            self.assertTrue(receipt_path.is_file())
            self.assertEqual(result["profile_triage_typed_receipt"]["status"], "no-remote-choice")


if __name__ == "__main__":
    unittest.main()
