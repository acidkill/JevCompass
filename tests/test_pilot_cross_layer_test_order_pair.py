"""Offline evidence, phase-order and privacy tests for the cross-layer pair runner."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_cross_layer_test_order_pair.py"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("pilot_cross_layer_test_order_pair", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


def event(kind: str, identifier: str, command: str, **extra) -> str:
    return json.dumps({
        "type": kind,
        "item": {
            "id": identifier,
            "type": "command_execution",
            "command": command,
            **extra,
        },
    })


def rank_payload(order=("integration", "unit"), status="remote-choice"):
    return json.dumps({
        "status": status,
        "ordered": [
            {"id": candidate, "kind": candidate,
             "command": runner.EXPECTED_CHOICES[candidate]}
            for candidate in order
        ],
        "required": [{"id": "full", "command": runner.REQUIRED_COMMAND}],
        "executed": False,
    })


def arm_events(prompt: str, *, edit_before_rank=True, edit_after_rank=False,
               rank_after_focus=False, useful_marker=True, duplicate=False,
               focused_exit=1, no_op_source_event=False):
    treatment = runner.TREATMENT_RANKING in prompt
    rows = []
    edit_cmd = "touch checkout/service.py" if no_op_source_event else "apply_patch -- checkout/service.py"
    rank_cmd = runner.RANK_COMMAND
    if treatment and edit_before_rank:
        rows.extend([
            event("item.started", "edit-before", edit_cmd),
            event("item.completed", "edit-before", edit_cmd, exit_code=0),
        ])
    if treatment and not rank_after_focus:
        order = ("integration", "unit")
        payload = rank_payload(order)
        rows.extend([
            event("item.started", "rank-1", rank_cmd),
            event("item.completed", "rank-1", rank_cmd, exit_code=0, aggregated_output=payload),
        ])
    if treatment and edit_after_rank:
        rows.extend([
            event("item.started", "edit-after", edit_cmd),
            event("item.completed", "edit-after", edit_cmd, exit_code=0),
        ])
    selected = "integration" if treatment else "unit"
    test_cmd = runner.EXPECTED_CHOICES[selected]
    output = (
        "FAIL: test_half_cent_rounds_half_up (tests.test_unit_checkout.CheckoutUnitTests)"
        if selected == "unit" else
        "FAIL: test_success_json_contract (tests.test_integration_checkout.CheckoutCliIntegrationTests)"
    ) if useful_marker else "AssertionError: unrelated 1 != 2"
    if focused_exit == 0:
        output = "Ran 3 tests in 0.01s\\n\\nOK"
    rows.extend([
        event("item.started", "focused-1", test_cmd),
        event("item.completed", "focused-1", test_cmd,
              exit_code=focused_exit, aggregated_output=output),
    ])
    if treatment and rank_after_focus:
        rows.extend([
            event("item.started", "rank-1", rank_cmd),
            event("item.completed", "rank-1", rank_cmd, exit_code=0,
                  aggregated_output=rank_payload(("integration", "unit"))),
        ])
    required_cmd = runner.REQUIRED_COMMAND
    rows.extend([
        event("item.started", "full-1", required_cmd),
        event("item.completed", "full-1", required_cmd, exit_code=0, aggregated_output="OK"),
    ])
    if duplicate:
        rows.append(event("item.completed", "focused-1", test_cmd, exit_code=1, aggregated_output=output))
    rows.append(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 18, "cached_input_tokens": 2,
        "cache_write_input_tokens": 0, "output_tokens": 5,
        "reasoning_output_tokens": 1,
    }}))
    base = time.monotonic()
    lines = list(rows)
    return lines, [base + i * .001 for i in range(len(lines))], None


class FakeProcess:
    returncode = 0

    def __init__(self, prompt):
        self.prompt = prompt


class CrossLayerRunnerTests(unittest.TestCase):
    def test_choice_validator_requires_exact_candidate_contract(self):
        valid = runner._validated_choice(rank_payload())
        self.assertEqual(valid["status"], "remote-choice")
        self.assertEqual(valid["candidate_ids"], ["integration", "unit"])
        abstention = runner._validated_choice(
            rank_payload(("unit", "integration"), status="no-remote-choice")
        )
        self.assertEqual(abstention["status"], "no-remote-choice")
        self.assertEqual(abstention["candidate_ids"], ["unit", "integration"])
        duplicate = json.loads(rank_payload())
        duplicate["ordered"][1]["id"] = "integration"
        self.assertEqual(runner._validated_choice(json.dumps(duplicate)),
                         {"status": "unscored", "candidate_ids": []})

    def test_only_matched_completed_failure_markers_count_once(self):
        lines, times, _ = arm_events(
            runner.BASE_PROMPT + runner.TREATMENT_RANKING, duplicate=True,
        )
        result = runner._event_receipts(lines, times, times[0])
        self.assertEqual(result["first_useful_error_status"], "observed")
        self.assertEqual(result["first_useful_error_candidate"], "integration")
        self.assertEqual(result["first_useful_error_event_count"], 1)
        self.assertEqual(result["focused_invocation_count"], 1)
        self.assertEqual(result["required_suite_exit"], 0)
        self.assertEqual(result["rank_phase_order_status"], "verified_order")
        self.assertEqual(result["choice_follow_status"], "followed")
        self.assertNotIn("FAIL:", json.dumps(result))

        unit_lines, unit_times, _ = arm_events(runner.BASE_PROMPT)
        unit_result = runner._event_receipts(unit_lines, unit_times, unit_times[0])
        self.assertEqual(unit_result["first_useful_error_candidate"], "unit")
        self.assertEqual(unit_result["first_useful_error_event_count"], 1)

    def test_unrelated_error_and_unmatched_completion_are_not_useful(self):
        lines, times, _ = arm_events(
            runner.BASE_PROMPT + runner.TREATMENT_RANKING, useful_marker=False,
        )
        # Remove the start of the selected focused event so its completion cannot match.
        lines = [line for line in lines if not (
            '"item.started"' in line and '"focused-1"' in line
        )]
        times = times[:len(lines)]
        result = runner._event_receipts(lines, times, times[0])
        self.assertIsNone(result["first_useful_error_ms"])
        self.assertEqual(result["focused_invocation_count"], 0)

    def test_rank_must_be_after_target_edit_and_before_first_focused(self):
        prompt = runner.BASE_PROMPT + runner.TREATMENT_RANKING
        cases = (
            arm_events(prompt, edit_before_rank=False),
            arm_events(prompt, edit_after_rank=True),
            arm_events(prompt, rank_after_focus=True),
        )
        for lines, times, _ in cases:
            with self.subTest():
                result = runner._event_receipts(lines, times, times[0])
                self.assertEqual(result["rank_phase_order_status"], "unscored")

    def setup_pair(self, temp: str):
        root = Path(temp)
        auth_home = root / "auth"
        auth_home.mkdir()
        (auth_home / "auth.json").write_text(
            '{"access_token":"LOCAL_TEST_SECRET"}', encoding="utf-8"
        )
        output = root / "private-output"
        calls = []
        real_popen = runner.subprocess.Popen

        def popen(command, *, cwd=None, env=None, **kwargs):
            if command[0] == "git":
                return real_popen(command, cwd=cwd, env=env, **kwargs)
            prompt = command[-1]
            fixture = Path(cwd)
            source = fixture / runner.CHANGED_FILE
            initial = source.read_text(encoding="utf-8")
            source.write_text(initial + "\n# offline runner simulation\n", encoding="utf-8")
            arm_auth = Path(env["CODEX_HOME"]) / "auth.json"
            calls.append({
                "command": command,
                "prompt": prompt,
                "fixture_hash": runner.engine.core.fixture_digest(fixture),
                "fixture_has_git": (fixture / ".git").exists(),
                "auth_bytes": arm_auth.read_bytes(),
                "auth_mode": stat.S_IMODE(arm_auth.stat().st_mode),
                "codex_home": env["CODEX_HOME"],
                "openrouter_key": env.get("OPENROUTER_API_KEY"),
                "network_option": "sandbox_workspace_write.network_access=true" in command,
                "treatment": runner.TREATMENT_RANKING in prompt,
            })
            return FakeProcess(prompt)

        return auth_home, output, calls, popen

    def execute_mock_pair(self, *, key_opt_in=False, missing_source_event=False, unchanged_at_rank=False, no_op_source_event=False, validation_exit=0):
        with tempfile.TemporaryDirectory() as temp:
            auth, output, calls, popen = self.setup_pair(temp)
            saved = {}

            def fake_collect(process, *, started, timeout, preserve_on_failure=False,
                             fixture=None, before_source=None, observation=None):
                result = arm_events(
                    process.prompt,
                    edit_before_rank=not (missing_source_event and runner.TREATMENT_RANKING in process.prompt),
                    focused_exit=0,
                    no_op_source_event=no_op_source_event and runner.TREATMENT_RANKING in process.prompt,
                )
                if runner.TREATMENT_RANKING in process.prompt:
                    observation["rank_source_snapshot_observed"] = True
                    observation["rank_source_snapshot_changed"] = not unchanged_at_rank
                    observation["rank_source_snapshot_ms"] = 4.0
                saved[runner.TREATMENT_RANKING in process.prompt] = result
                return result

            completed_process_run = runner.subprocess.CompletedProcess([], validation_exit, "", "")
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(auth)}, clear=False), \
                 mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "OPENROUTER_SECRET"}, clear=False), \
                 mock.patch.object(runner.subprocess, "Popen", side_effect=popen), \
                 mock.patch.object(runner, "_collect_events_live", side_effect=fake_collect), \
                 mock.patch.object(runner.subprocess, "run", return_value=completed_process_run):
                receipt = runner.run_pair(
                    codex="/fake/codex", model="gpt-5.6", reasoning_effort="high",
                    seed=3, output_dir=output, timeout=15,
                    allow_openrouter_key=key_opt_in,
                )
            output_evidence = {
                "receipt": (output / "receipt.json").read_text(encoding="utf-8"),
                "receipt_world_readable": bool(
                    (output / "receipt.json").stat().st_mode & stat.S_IROTH
                ),
                "final_artifacts": sorted(
                    path.name for path in output.glob("*-final.py")
                ),
                "arm_map_present": (output / "arm-map.json").is_file(),
            }
            return receipt, output_evidence, calls

    def test_pair_isolated_and_only_completed_when_post_change_phase_verified(self):
        receipt, output, calls = self.execute_mock_pair()
        self.assertEqual(receipt["status"], "completed", receipt.get("quality_gate_failures"))
        self.assertEqual(receipt["quality_gate_status"], "passed")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(runner.BASE_PROMPT in call["prompt"] for call in calls))
        self.assertEqual(
            sum(runner.TREATMENT_RANKING in call["prompt"] for call in calls), 1
        )
        self.assertEqual(calls[0]["fixture_hash"], calls[1]["fixture_hash"])
        self.assertTrue(all(not call["fixture_has_git"] for call in calls))
        self.assertEqual(calls[0]["auth_bytes"], calls[1]["auth_bytes"])
        self.assertEqual([call["auth_mode"] for call in calls], [0o600, 0o600])
        self.assertNotEqual(calls[0]["codex_home"], calls[1]["codex_home"])
        self.assertEqual([call["openrouter_key"] for call in calls], [None, None])
        self.assertTrue(all(not call["network_option"] for call in calls))
        treatment = next(arm for arm in receipt["arms"].values()
                         if arm["post_change_rank_phase_status"] == "verified")
        self.assertEqual(treatment["first_focused_candidate"], "integration")
        self.assertEqual(treatment["choice_follow_status"], "followed")
        self.assertTrue(all(
            all(code["exit_code"] == 0 for code in arm["independent_validation"].values())
            for arm in receipt["arms"].values()
        ))
        public = output["receipt"]
        self.assertNotIn("LOCAL_TEST_SECRET", public)
        self.assertNotIn("OPENROUTER_SECRET", public)
        self.assertNotIn("subtotal_cents must", public)
        self.assertNotIn(runner.BASE_PROMPT, public)
        self.assertNotIn(runner.UNIT_COMMAND, public)
        self.assertNotIn(runner.REQUIRED_COMMAND, public)
        self.assertNotIn("raw", public.lower())
        self.assertFalse(output["receipt_world_readable"])
        self.assertEqual(output["final_artifacts"], ["arm-a-final.py", "arm-b-final.py"])
        self.assertTrue(output["arm_map_present"])

    def test_missing_edit_event_leaves_treatment_phase_unscored_and_pair_failed(self):
        receipt, _output, _calls = self.execute_mock_pair(missing_source_event=True)
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("treatment_post_change_rank_phase_unverified",
                      receipt["quality_gate_failures"])
        treatment = next(
            arm for arm in receipt["arms"].values()
            if arm["post_change_rank_phase_status"] != "not_applicable"
        )
        self.assertEqual(treatment["post_change_rank_phase_status"], "unscored")
        self.assertEqual(treatment["post_change_rank_phase_reason"], "event_order_not_verified")

    def test_noop_touch_event_does_not_prove_source_changed_at_rank(self):
        receipt, _output, _calls = self.execute_mock_pair(
            unchanged_at_rank=True, no_op_source_event=True,
        )
        self.assertEqual(receipt["status"], "failed")
        treatment = next(
            arm for arm in receipt["arms"].values()
            if arm["post_change_rank_phase_status"] != "not_applicable"
        )
        self.assertEqual(treatment["rank_source_snapshot_observed"], True)
        self.assertEqual(treatment["rank_source_snapshot_changed"], False)
        self.assertEqual(treatment["post_change_rank_phase_status"], "unscored")
        self.assertEqual(treatment["post_change_rank_phase_reason"], "source_not_changed_at_rank")

    def test_supervisor_snapshots_actual_source_bytes_at_rank_event(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp)
            source = fixture / runner.CHANGED_FILE
            source.parent.mkdir(parents=True)
            source.write_text("value = 1\\n", encoding="utf-8")
            before = runner._digest_source(fixture)
            rank_event = {
                "type": "item.started",
                "item": {
                    "id": "rank",
                    "type": "command_execution",
                    "command": runner.RANK_COMMAND,
                },
            }
            code = (
                "import json,sys; "
                "p=sys.argv[1]; "
                "open(p,'a').write('value = 2\\\\n'); "
                "print(json.dumps(" + repr(rank_event) + "))"
            )
            started = time.monotonic()
            process = subprocess.Popen(
                [sys.executable, "-c", code, str(source)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            observation = {}
            lines, times, failure = runner._collect_events_live(
                process, started=started, timeout=2, fixture=fixture,
                before_source=before, observation=observation,
            )
            self.assertIsNone(failure)
            self.assertEqual(len(lines), 1)
            self.assertEqual(observation["rank_source_snapshot_observed"], True)
            self.assertEqual(observation["rank_source_snapshot_changed"], True)

    def test_noop_touch_path_event_does_not_change_live_rank_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp)
            source = fixture / runner.CHANGED_FILE
            source.parent.mkdir(parents=True)
            source.write_text("value = 1\\n", encoding="utf-8")
            before = runner._digest_source(fixture)
            no_op = {
                "type": "item.started",
                "item": {
                    "id": "touch",
                    "type": "command_execution",
                    "command": "touch checkout/service.py",
                },
            }
            rank = {
                "type": "item.started",
                "item": {
                    "id": "rank",
                    "type": "command_execution",
                    "command": runner.RANK_COMMAND,
                },
            }
            code = (
                "import json; "
                "print(json.dumps(" + repr(no_op) + ")); "
                "print(json.dumps(" + repr(rank) + "))"
            )
            started = time.monotonic()
            process = subprocess.Popen(
                [sys.executable, "-c", code],
                cwd=fixture, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            observation = {}
            lines, times, failure = runner._collect_events_live(
                process, started=started, timeout=2, fixture=fixture,
                before_source=before, observation=observation,
            )
            self.assertIsNone(failure)
            self.assertEqual(len(lines), 2)
            self.assertEqual(observation["rank_source_snapshot_observed"], True)
            self.assertEqual(observation["rank_source_snapshot_changed"], False)

    def test_openrouter_key_requires_explicit_opt_in_and_is_shared(self):
        receipt, _output, calls = self.execute_mock_pair(key_opt_in=True)
        self.assertEqual(receipt["status"], "completed", receipt.get("quality_gate_failures"))
        self.assertEqual([call["openrouter_key"] for call in calls],
                         ["OPENROUTER_SECRET", "OPENROUTER_SECRET"])
        self.assertTrue(all(call["network_option"] for call in calls))

    def test_independent_final_suite_failure_fails_pair_gate(self):
        receipt, _output, _calls = self.execute_mock_pair(validation_exit=1)
        self.assertEqual(receipt["status"], "failed")
        self.assertTrue(any("independent_final_validation_failed" in item
                            for item in receipt["quality_gate_failures"]))

    def test_timeout_validation_is_bounded_and_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                runner.run_pair(
                    codex="/fake/codex", model="gpt-5.6", reasoning_effort="high",
                    seed=1, output_dir=Path(temp) / "out", timeout=0,
                )

    def test_help_is_offline_and_live_flag_remains_explicit(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as error:
            runner.main(["--help"])
        self.assertEqual(error.exception.code, 0)
        self.assertIn("--live", output.getvalue())
        self.assertIn("--allow-openrouter-key", output.getvalue())


if __name__ == "__main__":
    unittest.main()
