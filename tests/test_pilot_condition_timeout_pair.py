"""Offline protocol, scope, privacy, and frozen-oracle tests for the local triage pair."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_condition_timeout_pair.py"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("pilot_condition_timeout_pair", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load the condition-timeout pilot runner")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def event(kind: str, event_id: str, command: str | None = None, **extra) -> str:
    item = {"id": event_id, "type": "command_execution", **extra}
    if command is not None:
        item["command"] = command
    return json.dumps({"type": kind, "item": item})


def arm_events(
    *,
    acknowledgement_token: str | None,
    useful_error: bool = True,
    focused_success: bool = True,
    full_success: bool = True,
    ack_before_tools: bool = True,
) -> tuple[list[str], list[float], None]:
    events = []
    if acknowledgement_token is not None and ack_before_tools:
        events.append(json.dumps({
            "type": "item.completed",
            "item": {
                "id": "initial-message",
                "type": "agent_message",
                "text": runner._acknowledgement_text(acknowledgement_token)
                + " I will inspect the local test and contract.",
            },
        }))
    failure_output = (
        "ERROR: test_preloaded_item_is_available_without_waiting (test_inbox.InboxContractTests)"
        + chr(10)
        + "TimeoutError: inbox-not-ready"
        if useful_error else "ERROR: arbitrary test output"
    )
    events.extend([
        event("item.started", "focused-initial", runner.FOCUSED_COMMAND),
        event("item.completed", "focused-initial", runner.FOCUSED_COMMAND,
              exit_code=1, aggregated_output=failure_output),
    ])
    if focused_success:
        events.extend([
            event("item.started", "focused-pass", runner.FOCUSED_COMMAND),
            event("item.completed", "focused-pass", runner.FOCUSED_COMMAND, exit_code=0),
        ])
    if full_success:
        events.extend([
            event("item.started", "full", runner.REQUIRED_COMMAND),
            event("item.completed", "full", runner.REQUIRED_COMMAND, exit_code=0),
        ])
    events.extend([
        json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 42,
            "cached_input_tokens": 5,
            "cache_write_input_tokens": 0,
            "output_tokens": 9,
            "reasoning_output_tokens": 2,
        }}),
        json.dumps({"type": "item.completed", "item": {
            "id": "final-message", "type": "agent_message",
            "text": "PRIVATE_FINAL_RESPONSE_MARKER",
        }}),
    ])
    base = time.monotonic()
    return events, [base + index * 0.001 for index in range(len(events))], None


class FakeProcess:
    returncode = 0

    def __init__(self, command):
        self.command = command
        self.prompt = command[-1]


class ConditionTimeoutPilotTests(unittest.TestCase):
    def test_verified_preflight_uses_production_local_timeout_triage(self):
        evidence = runner._review_fixture(runner.FIXTURE)
        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(
            evidence["fact_authority"],
            "caller_reviewed_fixture_evidence_not_triage_api_diagnosis",
        )
        prepared, instruction = runner._prepare_local_step(runner.FIXTURE, 1)
        self.assertEqual(prepared["triage_status"], "no-remote-choice")
        self.assertEqual(prepared["remote_invocation_count"], 0)
        self.assertFalse(prepared["triage_api_diagnosis"])
        self.assertEqual(prepared["next_check_id"], "timeout_nonterminating")
        self.assertIn("Check whether", instruction)
        self.assertNotIn("cause", instruction.lower())

    def test_preflight_abstains_if_concrete_source_evidence_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "fixture"
            runner.common._copy_identical_fixture(runner.FIXTURE, copied)
            source = copied / runner.SOURCE_FILE
            changed = source.read_text(encoding="utf-8").replace(
                "self._condition.notify_all()",
                "self._ready = True\n            self._condition.notify_all()",
                1,
            )
            source.write_text(changed, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reviewed timeout fixture bytes changed"):
                runner._review_fixture(copied)

    def test_preflight_rejects_anchor_preserving_source_test_and_contract_changes(self):
        for relative, suffix in (
            (runner.SOURCE_FILE, "\n# contradictory appended source\n"),
            (runner.TEST_FILE, "\n# contradictory appended test\n"),
            (runner.CONTRACT_FILE, "\nQueued values are never available.\n"),
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                copied = Path(temporary) / "fixture"
                runner.common._copy_identical_fixture(runner.FIXTURE, copied)
                target = copied / relative
                target.write_text(target.read_text(encoding="utf-8") + suffix, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "reviewed timeout fixture bytes changed"):
                    runner._review_fixture(copied)

    def test_event_receipts_require_expected_initial_failure_markers(self):
        lines, times, _ = arm_events(
            acknowledgement_token=runner.TREATMENT_ACK_TOKEN,
            useful_error=False,
        )
        started = times[0] - 0.001
        result = runner._event_receipts(
            lines, times, started,
            acknowledgement_token=runner.TREATMENT_ACK_TOKEN,
        )
        self.assertEqual(result["initial_focused_exit"], 1)
        self.assertEqual(result["initial_useful_error_status"], "unscored")
        self.assertIsNone(result["first_useful_error_ms"])
        self.assertTrue(result["focused_success_after_failure"])
        self.assertTrue(result["full_suite_pass_after_focused_pass"])
        self.assertTrue(result["initial_ack_before_tools"])

    def test_both_arm_acknowledgements_are_required_before_first_tool(self):
        for token in (runner.BASELINE_ACK_TOKEN, runner.TREATMENT_ACK_TOKEN):
            with self.subTest(token=token):
                lines, times, _ = arm_events(acknowledgement_token=token)
                started = times[0] - 0.001
                on_time = runner._event_receipts(
                    lines, times, started, acknowledgement_token=token,
                )
                self.assertTrue(on_time["initial_ack_before_tools"])

                missing_lines, missing_times, _ = arm_events(
                    acknowledgement_token=token, ack_before_tools=False,
                )
                missing = runner._event_receipts(
                    missing_lines, missing_times, missing_times[0] - 0.001,
                    acknowledgement_token=token,
                )
                self.assertFalse(missing["initial_ack_before_tools"])

                late_lines, late_times, _ = arm_events(acknowledgement_token=None)
                late_lines.insert(0, event("item.started", "first-tool", "pwd"))
                late_times.insert(0, late_times[0] - 0.001)
                late_lines.insert(1, json.dumps({
                    "type": "item.completed",
                    "item": {
                        "id": "late-ack",
                        "type": "agent_message",
                        "text": runner._acknowledgement_text(token),
                    },
                }))
                late_times.insert(1, late_times[0] + 0.0001)
                late = runner._event_receipts(
                    late_lines, late_times, late_times[0] - 0.002,
                    acknowledgement_token=token,
                )
                self.assertFalse(late["initial_ack_before_tools"])

    def test_required_initial_reproduction_workflow_is_shared_by_both_prompts(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt, calls, _output, _validation_launches = self._run_pair(temporary)
            prompts = {
                Path(call["cwd"]).name: call["prompt"] for call in calls
            }
            baseline_prompt = prompts["baseline-fixture"]
            treatment_prompt = prompts["treatment-fixture"]
            for prompt in (baseline_prompt, treatment_prompt):
                self.assertIn(runner.INITIAL_WORKFLOW_INSTRUCTION, prompt)
                workflow = runner.INITIAL_WORKFLOW_INSTRUCTION
                self.assertIn("Do not edit before the initial focused reproduction.", workflow)
                self.assertLess(
                    workflow.index("separate initial diagnostic command"),
                    workflow.index("Do not edit before the initial focused reproduction."),
                )
                self.assertLess(
                    workflow.index("Do not edit before the initial focused reproduction."),
                    workflow.index("rerun the focused test"),
                )
                self.assertLess(
                    workflow.index("rerun the focused test"),
                    workflow.index("run the required full suite"),
                )
            self.assertIn(
                runner._acknowledgement_text(runner.BASELINE_ACK_TOKEN), baseline_prompt,
            )
            self.assertIn(
                runner._acknowledgement_text(runner.TREATMENT_ACK_TOKEN), treatment_prompt,
            )
            self.assertTrue(receipt["prompt_hashes"]["same_required_workflow"])
            self.assertTrue(
                receipt["prompt_hashes"]["arm_specific_ack_and_treatment_next_check"],
            )

    def test_legacy_treatment_ack_after_first_tool_is_not_accepted(self):
        lines, times, _ = arm_events(acknowledgement_token=None)
        lines.insert(0, event("item.started", "first-tool", "pwd"))
        times.insert(0, times[0] - 0.001)
        lines.insert(1, json.dumps({
            "type": "item.completed",
            "item": {
                "id": "late-ack",
                "type": "agent_message",
                "text": runner._acknowledgement_text(runner.TREATMENT_ACK_TOKEN),
            },
        }))
        times.insert(1, times[0] + 0.0001)
        result = runner._event_receipts(
            lines, times, times[0] - 0.002,
            acknowledgement_token=runner.TREATMENT_ACK_TOKEN,
        )
        self.assertFalse(result["initial_ack_before_tools"])

    def _run_pair(
        self,
        temporary: str,
        *,
        useful_error: bool = True,
        full_success: bool = True,
        tamper_immutable: bool = False,
        omit_ack_token: str | None = None,
    ):
        temp_root = Path(temporary)
        auth_home = temp_root / "auth-home"
        auth_home.mkdir()
        (auth_home / "auth.json").write_text(
            '{"access_token":"AUTH_SECRET_MARKER"}', encoding="utf-8",
        )
        output = temp_root / "private-output"
        calls = []
        validation_launches = []
        real_popen = runner.subprocess.Popen
        real_collect = runner.core._collect_events

        def fake_popen(command, *, cwd=None, env=None, **kwargs):
            assert cwd is not None and env is not None
            if command[0] != "/fake/codex":
                validation_launches.append(Path(cwd).name)
                return real_popen(command, cwd=cwd, env=env, **kwargs)
            fixture = Path(cwd)
            if tamper_immutable:
                test_file = fixture / runner.TEST_FILE
                os.chmod(test_file, 0o600)
                with test_file.open("a", encoding="utf-8") as stream:
                    stream.write("\n# changed after agent execution\n")
            source = fixture / runner.SOURCE_FILE
            current = source.read_text(encoding="utf-8")
            anchor = "lambda: self._ready"
            if current.count(anchor) == 1:
                source.write_text(current.replace(anchor, "lambda: bool(self._items)", 1),
                                  encoding="utf-8")
            calls.append({
                "command": command,
                "cwd": str(cwd),
                "env": dict(env),
                "auth": (Path(env["CODEX_HOME"]) / "auth.json").read_bytes(),
                "prompt": command[-1],
            })
            return FakeProcess(command)

        def collect(process, *, started, timeout, preserve_on_failure=False):
            if isinstance(process, FakeProcess):
                is_treatment = runner._acknowledgement_text(
                    runner.TREATMENT_ACK_TOKEN,
                ) in process.prompt
                acknowledgement_token = (
                    runner.TREATMENT_ACK_TOKEN if is_treatment
                    else runner.BASELINE_ACK_TOKEN
                )
                return arm_events(
                    acknowledgement_token=(
                        None if acknowledgement_token == omit_ack_token
                        else acknowledgement_token
                    ),
                    useful_error=useful_error,
                    full_success=full_success,
                )
            return real_collect(
                process, started=started, timeout=timeout,
                preserve_on_failure=preserve_on_failure,
            )

        with (
            mock.patch.dict(os.environ, {
                "CODEX_HOME": str(auth_home),
                "OPENROUTER_API_KEY": "OPENROUTER_SECRET_MARKER",
            }, clear=False),
            mock.patch.object(runner, "_verify_codex_version", return_value=True),
            mock.patch.object(runner.subprocess, "Popen", side_effect=fake_popen),
            mock.patch.object(runner.core, "_collect_events", side_effect=collect),
        ):
            receipt = runner.run_pair(
                codex="/fake/codex",
                model="gpt-6-sol",
                reasoning_effort="high",
                timeout=5,
                seed=8,
                output_dir=output,
            )
        return receipt, calls, output, validation_launches

    def test_pair_is_keyless_blind_immutable_and_independently_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt, calls, output, _validation_launches = self._run_pair(temporary)

            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["advice_utility_status"], "unscored")
            self.assertEqual(receipt["openrouter_key_allowed"], False)
            self.assertEqual(receipt["remote_triage_invocations"], 0)
            self.assertGreater(receipt["preparation_ms"], 0)
            self.assertTrue(receipt["prompt_hashes"]["same_shared_evidence"])
            self.assertTrue(receipt["prompt_hashes"]["same_required_workflow"])
            self.assertTrue(
                receipt["prompt_hashes"]["arm_specific_ack_and_treatment_next_check"],
            )
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["auth"], calls[1]["auth"])
            for call in calls:
                self.assertNotIn("OPENROUTER_API_KEY", call["env"])
                self.assertNotIn("OPENROUTER_SECRET_MARKER", repr(call["env"]))
                self.assertEqual(
                    call["command"][call["command"].index("--model") + 1],
                    "gpt-6-sol",
                )
                self.assertIn("model_reasoning_effort=high", call["command"])
                self.assertNotIn("sandbox_workspace_write.network_access=true", call["command"])
            mapping = json.loads((output / "arm-map.json").read_text(encoding="utf-8"))
            baseline = receipt["arms"]["arm-a" if mapping["arm-a"] == "baseline" else "arm-b"]
            treatment = receipt["arms"]["arm-a" if mapping["arm-a"] == "treatment" else "arm-b"]
            self.assertEqual(baseline["initial_focused_exit"], 1)
            self.assertEqual(treatment["initial_focused_exit"], 1)
            self.assertTrue(baseline["immutable_files_preserved"])
            self.assertTrue(treatment["immutable_files_preserved"])
            self.assertNotEqual(baseline["source_sha256_before"], baseline["source_sha256_after"])
            self.assertNotEqual(treatment["source_sha256_before"], treatment["source_sha256_after"])
            for arm in (baseline, treatment):
                self.assertEqual(arm["full_suite_exit"], 0)
                self.assertEqual(arm["independent_frozen_validation"]["status"], "passed")
                self.assertGreaterEqual(
                    arm["total_including_preparation_ms"],
                    receipt["preparation_ms"],
                )
            self.assertTrue(receipt["baseline_ack_before_tools"])
            self.assertTrue(receipt["treatment_ack_before_tools"])
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            for path in output.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            serialized = (output / "receipt.json").read_text(encoding="utf-8")
            for marker in (
                "AUTH_SECRET_MARKER",
                "OPENROUTER_SECRET_MARKER",
                "PRIVATE_FINAL_RESPONSE_MARKER",
                "lambda: bool(self._items)",
                runner._acknowledgement_text(runner.TREATMENT_ACK_TOKEN),
            ):
                self.assertNotIn(marker, serialized)

    def test_missing_failure_markers_or_full_success_keeps_pair_incomplete(self):
        for useful_error, full_success in ((False, True), (True, False)):
            with self.subTest(useful_error=useful_error, full_success=full_success), tempfile.TemporaryDirectory() as temporary:
                receipt, _calls, _output, _validation_launches = self._run_pair(
                    temporary, useful_error=useful_error, full_success=full_success,
                )
                self.assertEqual(receipt["status"], "incomplete")
                self.assertEqual(receipt["advice_utility_status"], "unscored")
                arms = receipt["arms"].values()
                self.assertTrue(all(arm["initial_useful_error_status"] == (
                    "unscored" if not useful_error else "observed"
                ) for arm in arms))

    def test_pair_gate_requires_acknowledgement_from_both_arms(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _calls, _output, _validation_launches = self._run_pair(
                temporary, omit_ack_token=runner.BASELINE_ACK_TOKEN,
            )

            self.assertEqual(receipt["status"], "incomplete")
            mapping = json.loads((_output / "arm-map.json").read_text(encoding="utf-8"))
            baseline_label = next(label for label, arm in mapping.items() if arm == "baseline")
            treatment_label = next(label for label, arm in mapping.items() if arm == "treatment")
            self.assertFalse(receipt["baseline_ack_before_tools"])
            self.assertTrue(receipt["treatment_ack_before_tools"])
            self.assertFalse(receipt["arm_gates"][baseline_label])
            self.assertTrue(receipt["arms"][treatment_label]["initial_ack_before_tools"])

    def test_immutable_fixture_tampering_skips_independent_test_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _calls, _output, validation_launches = self._run_pair(
                temporary, tamper_immutable=True,
            )

            self.assertEqual(receipt["status"], "incomplete")
            mapping = receipt["arm_gates"]
            self.assertTrue(all(gate is False for gate in mapping.values()))
            self.assertEqual(validation_launches, ["preflight-fixture"])
            for arm in receipt["arms"].values():
                self.assertFalse(arm["immutable_files_preserved"])
                self.assertEqual(
                    arm["independent_frozen_validation"]["status"], "failed",
                )
                self.assertEqual(
                    arm["independent_frozen_validation"]["reason"],
                    "immutable_fixture_changed",
                )
                self.assertEqual(
                    arm["independent_frozen_validation"]["focused"]["status"],
                    "skipped",
                )


if __name__ == "__main__":
    unittest.main()
