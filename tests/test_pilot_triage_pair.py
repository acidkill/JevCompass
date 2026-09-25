"""Offline smoke, privacy, timeout, and validation tests for triage pairs."""
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
SCRIPT = ROOT / "scripts" / "pilot_triage_pair.py"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("pilot_triage_pair", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


def command_event(event_type: str, identifier: str, command: str, **extra):
    return json.dumps({
        "type": event_type,
        "item": {
            "id": identifier,
            "type": "command_execution",
            "command": command,
            **extra,
        },
    })


def arm_events(*, treatment: bool, triage_payload: str | None = None):
    initial_output = (
        "ERROR: test_store (unittest.loader._FailedTest.test_store)\n"
        "ModuleNotFoundError: No module named 'parcelcache.codec'\n"
        "PRIVATE_RAW_OUTPUT"
    )
    events = [
        command_event("item.started", "initial", runner.FOCUSED_COMMAND),
        command_event("item.completed", "initial", runner.FOCUSED_COMMAND,
                      exit_code=1, aggregated_output=initial_output),
    ]
    if treatment:
        command = " ".join(runner._triage_command(1))
        payload = triage_payload or json.dumps({
            "observed_exit_status": 1,
            "test_failed": True,
            "status": "remote-choice",
            "steps": [{"id": "import_path_changed"}, {"id": "import_module_missing"}],
            "executed": False,
        })
        events.extend([
            command_event("item.started", "triage", command),
            command_event("item.completed", "triage", command, exit_code=0,
                          aggregated_output=payload),
        ])
    events.extend([
        command_event("item.started", "focused-after", runner.FOCUSED_COMMAND),
        command_event("item.completed", "focused-after", runner.FOCUSED_COMMAND, exit_code=0),
        command_event("item.started", "full", runner.REQUIRED_COMMAND),
        command_event("item.completed", "full", runner.REQUIRED_COMMAND, exit_code=0),
        json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 31, "cached_input_tokens": 4,
            "cache_write_input_tokens": 0, "output_tokens": 8,
            "reasoning_output_tokens": 2,
        }}),
    ])
    base = time.monotonic()
    return events, [base + index * 0.001 for index in range(len(events))], None


class FakeProcess:
    returncode = 0

    def __init__(self, prompt: str):
        self.prompt = prompt


class TriagePairTests(unittest.TestCase):
    def _run_smoke(self, temp: str, *, failure: str | None = None, triage_payload=None):
        root = Path(temp)
        auth_home = root / "auth"
        auth_home.mkdir()
        (auth_home / "auth.json").write_text(
            '{"access_token":"AUTH_SECRET_MARKER"}', encoding="utf-8",
        )
        output = root / "receipts"
        calls = []
        real_popen = runner.subprocess.Popen

        def popen(command, *, cwd=None, env=None, **kwargs):
            if command[0] == "git":
                return real_popen(command, cwd=cwd, env=env, **kwargs)
            fixture = Path(cwd)
            original = fixture.joinpath(runner.CHANGED_FILE).read_text(encoding="utf-8")
            fixture.joinpath(runner.CHANGED_FILE).write_text(
                original.replace("from .codec import", "from .wire import"),
                encoding="utf-8",
            )
            calls.append({
                "command": command,
                "prompt": command[-1],
                "cwd": str(cwd),
                "env": dict(env),
                "auth": (Path(env["CODEX_HOME"]) / "auth.json").read_bytes(),
            })
            return FakeProcess(command[-1])

        def collect(process, *, started, timeout, preserve_on_failure=False):
            treatment = "After the initial focused test command" in process.prompt if hasattr(process, "prompt") else False
            lines, times, _ = arm_events(treatment=treatment, triage_payload=triage_payload)
            return lines, times, failure

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(auth_home)}, clear=False), \
             mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "OPENROUTER_SECRET"}, clear=False), \
             mock.patch.object(runner.subprocess, "Popen", side_effect=popen), \
             mock.patch.object(runner.core, "_collect_events", side_effect=collect):
            receipt = runner.run_pair(
                codex="/fake/codex", model="gpt-5.6", reasoning_effort="high",
                seed=1, output_dir=output,
            )
        return receipt, output, calls

    def test_mock_pair_smoke_parity_validation_and_private_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt, output, calls = self._run_smoke(temp)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["prompt"].split("\n\nAfter the initial")[0], runner.BASE_PROMPT)
            self.assertEqual(calls[1]["prompt"].split("\n\nAfter the initial")[0], runner.BASE_PROMPT)
            self.assertEqual(calls[0]["cwd"] != calls[1]["cwd"], True)
            self.assertEqual(calls[0]["auth"], calls[1]["auth"])
            self.assertEqual(calls[0]["command"][:-1], calls[1]["command"][:-1])
            self.assertNotEqual(calls[0]["env"]["CODEX_HOME"], calls[1]["env"]["CODEX_HOME"])
            self.assertIn("workspace-write", calls[0]["command"])
            self.assertNotIn("sandbox_workspace_write.network_access=true", calls[0]["command"])
            self.assertNotIn("sandbox_workspace_write.network_access=true", calls[1]["command"])
            self.assertNotIn("OPENROUTER_API_KEY", calls[0]["env"])
            self.assertNotIn("OPENROUTER_API_KEY", calls[1]["env"])
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["fixture_parity_sha256"][:8], receipt["arms"]["arm-a"]["fixture_sha256_before"][:8])
            self.assertTrue(all(arm["subsequent_focused_exit_code"] == 0 for arm in receipt["arms"].values()))
            self.assertTrue(all(arm["required_suite_exit"] == 0 for arm in receipt["arms"].values()))
            self.assertTrue(any(arm["triage"]["status"] == "remote-choice" for arm in receipt["arms"].values()))
            public = (output / "receipt.json").read_text(encoding="utf-8")
            for secret in (
                "AUTH_SECRET_MARKER", "OPENROUTER_SECRET", "PRIVATE_RAW_OUTPUT",
                runner.BASE_PROMPT, runner.FOCUSED_COMMAND, runner.REQUIRED_COMMAND,
                str(ROOT), runner.CHANGED_FILE,
            ):
                self.assertNotIn(secret, public)
            self.assertFalse((output / "receipt.json").stat().st_mode & stat.S_IROTH)
            self.assertTrue((output / "arm-map.json").is_file())
            self.assertTrue((output / "arm-a-source.bin").is_file())
            self.assertTrue((output / "arm-b-source.bin").is_file())
            self.assertNotIn("treatment", public)

    def test_useful_error_requires_both_exact_markers_and_failure_exit(self):
        started = time.monotonic()
        good = (
            "ERROR: test_store (unittest.loader._FailedTest.test_store)\n"
            "ModuleNotFoundError: No module named 'parcelcache.codec'"
        )
        lines = [
            command_event("item.started", "focus", runner.FOCUSED_COMMAND),
            command_event("item.completed", "focus", runner.FOCUSED_COMMAND,
                          exit_code=1, aggregated_output=good),
        ]
        result = runner._event_receipts(lines, [started + 0.25, started + 0.5], started)
        self.assertEqual(result["initial_focused_exit_code"], 1)
        self.assertTrue(result["initial_useful_error_match"])
        self.assertEqual(result["initial_useful_error_ms"], 500.0)
        for output, exit_code in (
            (good.replace("_FailedTest.test_store", "_FailedTest.other"), 1),
            (good.replace("parcelcache.codec", "parcelcache.other"), 1),
            (good, 0),
        ):
            mismatch = [
                lines[0],
                command_event("item.completed", "focus", runner.FOCUSED_COMMAND,
                              exit_code=exit_code, aggregated_output=output),
            ]
            receipt = runner._event_receipts(mismatch, [started + 0.25, started + 0.5], started)
            self.assertFalse(receipt["initial_useful_error_match"])
            self.assertIsNone(receipt["initial_useful_error_ms"])

    def test_invalid_triage_ids_and_wrong_original_exit_are_unscored(self):
        for payload in (
            json.dumps({
                "observed_exit_status": 1, "test_failed": True, "status": "remote-choice",
                "steps": [{"id": "unexpected"}], "executed": False,
            }),
            json.dumps({
                "observed_exit_status": 0, "test_failed": False, "status": "remote-choice",
                "steps": [{"id": "import_path_changed"}], "executed": False,
            }),
        ):
            events, times, _ = arm_events(treatment=True, triage_payload=payload)
            receipt = runner._event_receipts(events, times, times[0])
            self.assertEqual(receipt["triage_output_status"], "invalid_output")
            self.assertEqual(receipt["triage"], {"status": "unscored", "candidate_ids": []})

    def test_triage_before_initial_failure_is_not_accepted(self):
        events = [
            command_event("item.started", "triage", " ".join(runner._triage_command(1))),
            command_event("item.completed", "triage", " ".join(runner._triage_command(1)),
                          exit_code=0, aggregated_output='{"status":"remote-choice"}'),
            command_event("item.started", "initial", runner.FOCUSED_COMMAND),
            command_event("item.completed", "initial", runner.FOCUSED_COMMAND, exit_code=1),
        ]
        base = time.monotonic()
        receipt = runner._event_receipts(events, [base + i / 100 for i in range(4)], base)
        self.assertTrue(receipt["triage_cli_invocation_observed"])
        self.assertFalse(receipt["triage_after_initial_failure"])
        self.assertEqual(receipt["triage"], {"status": "unscored", "candidate_ids": []})

    def test_unrelated_initial_failure_fails_even_when_later_checks_pass(self):
        events = [
            command_event("item.started", "initial", runner.FOCUSED_COMMAND),
            command_event(
                "item.completed", "initial", runner.FOCUSED_COMMAND, exit_code=1,
                aggregated_output=(
                    "ERROR: test_store (unittest.loader._FailedTest.test_store)\\n"
                    "PermissionError: local fixture is unavailable"
                ),
            ),
            command_event("item.started", "focus-2", runner.FOCUSED_COMMAND),
            command_event("item.completed", "focus-2", runner.FOCUSED_COMMAND, exit_code=0),
            command_event("item.started", "full", runner.REQUIRED_COMMAND),
            command_event("item.completed", "full", runner.REQUIRED_COMMAND, exit_code=0),
        ]
        started = time.monotonic()
        receipt = runner._event_receipts(
            events, [started + index / 100 for index in range(len(events))], started,
        )
        arm = {"cli_status": "completed", **receipt}
        self.assertEqual(arm["initial_focused_exit_code"], 1)
        self.assertFalse(arm["initial_useful_error_match"])
        self.assertEqual(arm["subsequent_focused_exit_code"], 0)
        self.assertEqual(arm["required_suite_exit"], 0)
        self.assertFalse(runner._arm_passed(
            arm, treatment=False, artifact_changed=True,
        ))

    def test_process_checks_are_separate_from_final_artifact_gate(self):
        events, times, _ = arm_events(treatment=False)
        receipt = runner._event_receipts(events, times, times[0])
        arm = {"cli_status": "completed", **receipt}
        self.assertTrue(arm["initial_useful_error_match"])
        self.assertEqual(arm["subsequent_focused_exit_code"], 0)
        self.assertEqual(arm["required_suite_exit"], 0)
        self.assertFalse(runner._arm_passed(
            arm, treatment=False, artifact_changed=False,
        ))
        self.assertTrue(runner._arm_passed(
            arm, treatment=False, artifact_changed=True,
        ))

    def test_timeout_marks_pair_failed_and_keeps_no_raw_output(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt, output, _ = self._run_smoke(temp, failure="timeout")
            self.assertEqual(receipt["status"], "failed")
            self.assertTrue(all(arm["failure"] == "timeout" for arm in receipt["arms"].values()))
            serialized = (output / "receipt.json").read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE_RAW_OUTPUT", serialized)
            self.assertNotIn(runner.BASE_PROMPT, serialized)


if __name__ == "__main__":
    unittest.main()
