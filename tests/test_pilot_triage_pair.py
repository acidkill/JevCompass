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
    events.extend([
        command_event("item.started", "probe", runner.DISCRIMINATOR_COMMAND),
        command_event(
            "item.completed", "probe", runner.DISCRIMINATOR_COMMAND, exit_code=0,
            aggregated_output="\n".join(runner.DISCRIMINATOR_OUTPUT),
        ),
    ])
    if treatment:
        command = " ".join(runner._triage_command(1))
        payload = triage_payload or json.dumps({
            "observed_exit_status": 1,
            "test_failed": True,
            "status": "no-remote-choice",
            "steps": [{"id": "import_path_changed"}],
            "executed": False,
            "decision_usage": {"input_tokens": 23, "output_tokens": 7, "cost_usd": 0.0042},
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
            self.assertTrue(all(arm["discriminator_successful"] for arm in receipt["arms"].values()))
            self.assertTrue(all(
                isinstance(arm["first_successful_discriminator_ms"], (int, float))
                for arm in receipt["arms"].values()
            ))
            treatment_arm = next(
                arm for arm in receipt["arms"].values()
                if arm["triage_cli_invocation_observed"]
            )
            self.assertEqual(treatment_arm["triage_usage"], {
                "scope": "exact_expected_triage_cli_invocations",
                "status": "complete", "invocation_count": 1,
                "input_tokens": 23, "output_tokens": 7,
                "jev_provider_cost_usd": 0.0042,
                "codex_billing_estimate": None,
            })
            self.assertTrue(all(
                arm["triage_usage"]["codex_billing_estimate"] is None
                for arm in receipt["arms"].values()
            ))
            self.assertTrue(any(
                arm["triage"]["status"] == "no-remote-choice"
                and arm["triage"]["candidate_ids"] == ["import_path_changed"]
                for arm in receipt["arms"].values()
            ))
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
                "observed_exit_status": 1, "test_failed": True, "status": "no-remote-choice",
                "steps": [{"id": "unexpected"}], "executed": False,
            }),
            json.dumps({
                "observed_exit_status": 0, "test_failed": False, "status": "no-remote-choice",
                "steps": [{"id": "import_path_changed"}], "executed": False,
            }),
            json.dumps({
                "observed_exit_status": 1, "test_failed": True, "status": "remote-choice",
                "steps": [{"id": "import_path_changed"}], "executed": False,
            }),
        ):
            events, times, _ = arm_events(treatment=True, triage_payload=payload)
            receipt = runner._event_receipts(events, times, times[0])
            self.assertEqual(receipt["triage_output_status"], "invalid_output")
            self.assertEqual(receipt["triage"], {"status": "unscored", "candidate_ids": []})

    def test_usage_survives_uncertain_choice_without_passing_gate(self):
        payload = json.dumps({
            "observed_exit_status": 1, "test_failed": True,
            "status": "remote-choice", "steps": [], "executed": False,
            "decision_usage": {
                "input_tokens": 41, "output_tokens": 13, "cost_usd": 0.0125,
                "private_provider_detail": "PRIVATE_USAGE_DETAIL",
            },
            "private_raw_field": "PRIVATE_RAW_FIELD",
        })
        events, times, _ = arm_events(treatment=True, triage_payload=payload)
        receipt = runner._event_receipts(events, times, times[0])
        self.assertEqual(receipt["triage"]["status"], "unscored")
        self.assertEqual(receipt["triage_output_status"], "invalid_output")
        self.assertEqual(receipt["triage_usage"], {
            "scope": "exact_expected_triage_cli_invocations",
            "status": "complete", "invocation_count": 1,
            "input_tokens": 41, "output_tokens": 13,
            "jev_provider_cost_usd": 0.0125,
            "codex_billing_estimate": None,
        })
        arm = {"cli_status": "completed", **receipt}
        self.assertFalse(runner._arm_passed(
            arm, treatment=True, artifact_changed=True,
        ))
        serialized = json.dumps(receipt)
        self.assertNotIn("PRIVATE_USAGE_DETAIL", serialized)
        self.assertNotIn("PRIVATE_RAW_FIELD", serialized)

    def test_invalid_or_incomplete_usage_stays_unknown(self):
        invalid_payload = json.dumps({
            "status": "no-remote-choice", "decision_usage": {
                "input_tokens": True, "output_tokens": 2, "cost_usd": float("nan"),
            },
        })
        events, times, _ = arm_events(treatment=True, triage_payload=invalid_payload)
        receipt = runner._event_receipts(events, times, times[0])
        self.assertEqual(receipt["triage_usage"]["status"], "incomplete")
        self.assertIsNone(receipt["triage_usage"]["input_tokens"])
        self.assertIsNone(receipt["triage_usage"]["jev_provider_cost_usd"])

        started = [
            command_event("item.started", "focus", runner.FOCUSED_COMMAND),
            command_event("item.completed", "focus", runner.FOCUSED_COMMAND, exit_code=1),
            command_event("item.started", "triage", " ".join(runner._triage_command(1))),
        ]
        base = time.monotonic()
        partial = runner._event_receipts(
            started, [base + i / 100 for i in range(len(started))], base,
        )
        self.assertEqual(partial["triage_usage"]["status"], "incomplete")
        self.assertEqual(partial["triage_usage"]["invocation_count"], 1)
        self.assertIsNone(partial["triage_usage"]["jev_provider_cost_usd"])

    def test_duplicate_events_do_not_double_count_and_null_cost_stays_unknown(self):
        payload = json.dumps({
            "status": "no-remote-choice", "decision_usage": {
                "input_tokens": 5, "output_tokens": 2, "cost_usd": None,
            },
        })
        events, times, _ = arm_events(treatment=True, triage_payload=payload)
        triage_start = next(line for line in events if '"id": "triage"' in line and '"type": "item.started"' in line)
        triage_done = next(line for line in events if '"id": "triage"' in line and '"type": "item.completed"' in line)
        start_index = events.index(triage_start)
        end_index = events.index(triage_done)
        events.insert(start_index + 1, triage_start)
        times.insert(start_index + 1, times[start_index])
        events.extend([triage_start, triage_done])
        times.extend([times[-1] + 0.001, times[-1] + 0.002])
        receipt = runner._event_receipts(events, times, times[0])
        self.assertEqual(receipt["triage_usage"]["invocation_count"], 1)
        self.assertEqual(receipt["triage_usage"]["input_tokens"], 5)
        self.assertEqual(receipt["triage_usage"]["output_tokens"], 2)
        self.assertIsNone(receipt["triage_usage"]["jev_provider_cost_usd"])

    def test_multiple_calls_sum_cost_and_extreme_values_are_rejected(self):
        events, times, _ = arm_events(treatment=True)
        command = " ".join(runner._triage_command(1))
        payload = json.dumps({"decision_usage": {
            "input_tokens": 10, "output_tokens": 3, "cost_usd": 0.001,
        }})
        events.extend([
            command_event("item.started", "triage-two", command),
            command_event("item.completed", "triage-two", command,
                          exit_code=0, aggregated_output=payload),
        ])
        times.extend([times[-1] + 0.01, times[-1] + 0.02])
        receipt = runner._event_receipts(events, times, times[0])
        usage = receipt["triage_usage"]
        self.assertEqual(usage["invocation_count"], 2)
        self.assertEqual(usage["input_tokens"], 33)
        self.assertAlmostEqual(usage["jev_provider_cost_usd"], 0.0052)
        for cost in (10**400, float("inf"), -1, True):
            with self.subTest(cost=str(cost)[:20]):
                self.assertIsNone(runner._validated_decision_usage(json.dumps({
                    "decision_usage": {"input_tokens": 1, "output_tokens": 2,
                                       "cost_usd": cost},
                })))

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
        self.assertFalse(receipt["triage_after_discriminator"])
        self.assertEqual(receipt["triage"], {"status": "unscored", "candidate_ids": []})

    def test_discriminator_requires_exact_command_and_exact_success_output(self):
        initial_output = (
            "ERROR: test_store (unittest.loader._FailedTest.test_store)\n"
            "ModuleNotFoundError: No module named 'parcelcache.codec'"
        )

        def receipt_for(command, output, exit_code=0):
            events = [
                command_event("item.started", "initial", runner.FOCUSED_COMMAND),
                command_event("item.completed", "initial", runner.FOCUSED_COMMAND,
                              exit_code=1, aggregated_output=initial_output),
                command_event("item.started", "probe", command),
                command_event("item.completed", "probe", command,
                              exit_code=exit_code, aggregated_output=output),
            ]
            started = time.monotonic()
            times = [started + index / 100 for index in range(len(events))]
            return runner._event_receipts(events, times, started)

        successful = receipt_for(
            runner.DISCRIMINATOR_COMMAND, "\n".join(runner.DISCRIMINATOR_OUTPUT),
        )
        self.assertTrue(successful["discriminator_invocation_observed"])
        self.assertTrue(successful["discriminator_successful"])
        self.assertEqual(successful["first_successful_discriminator_ms"], 30.0)

        unsupported = receipt_for(
            "python -c \\\"print('package_present')\\\"",
            "\n".join(runner.DISCRIMINATOR_OUTPUT),
        )
        self.assertFalse(unsupported["discriminator_invocation_observed"])
        self.assertFalse(unsupported["discriminator_successful"])
        self.assertIsNone(unsupported["first_successful_discriminator_ms"])

        wrong_output = receipt_for(
            runner.DISCRIMINATOR_COMMAND,
            "package_present\ntarget_module_absent\nPRIVATE_UNVERIFIED_CLAIM",
        )
        self.assertTrue(wrong_output["discriminator_invocation_observed"])
        self.assertFalse(wrong_output["discriminator_successful"])
        self.assertIsNone(wrong_output["first_successful_discriminator_ms"])

    def test_triage_before_verified_discriminator_is_rejected(self):
        initial_output = (
            "ERROR: test_store (unittest.loader._FailedTest.test_store)\n"
            "ModuleNotFoundError: No module named 'parcelcache.codec'"
        )
        command = " ".join(runner._triage_command(1))
        triage = json.dumps({
            "observed_exit_status": 1,
            "test_failed": True,
            "status": "no-remote-choice",
            "steps": [{"id": "import_path_changed"}],
            "executed": False,
        })
        events = [
            command_event("item.started", "initial", runner.FOCUSED_COMMAND),
            command_event("item.completed", "initial", runner.FOCUSED_COMMAND,
                          exit_code=1, aggregated_output=initial_output),
            command_event("item.started", "triage", command),
            command_event("item.completed", "triage", command,
                          exit_code=0, aggregated_output=triage),
            command_event("item.started", "probe", runner.DISCRIMINATOR_COMMAND),
            command_event(
                "item.completed", "probe", runner.DISCRIMINATOR_COMMAND, exit_code=0,
                aggregated_output="\n".join(runner.DISCRIMINATOR_OUTPUT),
            ),
        ]
        started = time.monotonic()
        times = [started + index / 100 for index in range(len(events))]
        receipt = runner._event_receipts(events, times, started)
        self.assertTrue(receipt["discriminator_successful"])
        self.assertTrue(receipt["triage_cli_invocation_observed"])
        self.assertFalse(receipt["triage_after_discriminator"])
        self.assertTrue(receipt["triage_invalid_invocation_observed"])
        self.assertNotEqual(receipt["triage_output_status"], "valid_local_resolution")
        self.assertEqual(receipt["triage"], {"status": "unscored", "candidate_ids": []})

    def test_triage_command_requires_all_verified_observations(self):
        initial_output = (
            "ERROR: test_store (unittest.loader._FailedTest.test_store)\n"
            "ModuleNotFoundError: No module named 'parcelcache.codec'"
        )
        valid = " ".join(runner._triage_command(1))
        invalid = valid.replace(" --import-observation replacement_module_present", "")
        payload = json.dumps({
            "observed_exit_status": 1,
            "test_failed": True,
            "status": "no-remote-choice",
            "steps": [{"id": "import_path_changed"}],
            "executed": False,
        })
        events = [
            command_event("item.started", "initial", runner.FOCUSED_COMMAND),
            command_event("item.completed", "initial", runner.FOCUSED_COMMAND,
                          exit_code=1, aggregated_output=initial_output),
            command_event("item.started", "probe", runner.DISCRIMINATOR_COMMAND),
            command_event("item.completed", "probe", runner.DISCRIMINATOR_COMMAND,
                          exit_code=0, aggregated_output="\n".join(runner.DISCRIMINATOR_OUTPUT)),
            command_event("item.started", "triage", invalid),
            command_event("item.completed", "triage", invalid,
                          exit_code=0, aggregated_output=payload),
        ]
        started = time.monotonic()
        times = [started + index / 100 for index in range(len(events))]
        receipt = runner._event_receipts(events, times, started)
        self.assertTrue(receipt["discriminator_successful"])
        self.assertTrue(receipt["triage_cli_invocation_observed"])
        self.assertTrue(receipt["triage_invalid_invocation_observed"])
        self.assertFalse(receipt["triage_after_discriminator"])
        self.assertEqual(receipt["triage"], {"status": "unscored", "candidate_ids": []})

    def test_triage_output_must_be_local_resolution_for_preserved_exit(self):
        payload = json.dumps({
            "observed_exit_status": 1,
            "test_failed": True,
            "status": "remote-choice",
            "steps": [{"id": "import_path_changed"}],
            "executed": False,
        })
        events, times, _ = arm_events(treatment=True, triage_payload=payload)
        receipt = runner._event_receipts(events, times, times[0])
        self.assertTrue(receipt["triage_after_discriminator"])
        self.assertEqual(receipt["triage_cli_exit_code"], 0)
        self.assertEqual(receipt["triage_output_status"], "invalid_output")
        self.assertEqual(receipt["triage"], {"status": "unscored", "candidate_ids": []})

    def test_unrelated_initial_failure_fails_even_when_later_checks_pass(self):
        events = [
            command_event("item.started", "initial", runner.FOCUSED_COMMAND),
            command_event(
                "item.completed", "initial", runner.FOCUSED_COMMAND, exit_code=1,
                aggregated_output=(
                    "ERROR: test_store (unittest.loader._FailedTest.test_store)\n"
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

    def test_baseline_equivalent_diagnostic_does_not_require_exact_probe(self):
        events, _, _ = arm_events(treatment=False)
        events = [line for line in events if not (
            '"id": "probe"' in line
        )]
        started = time.monotonic()
        receipt = runner._event_receipts(
            events, [started + index / 100 for index in range(len(events))], started,
        )
        arm = {"cli_status": "completed", **receipt}
        self.assertFalse(arm["discriminator_successful"])
        self.assertTrue(arm["initial_useful_error_match"])
        self.assertTrue(runner._arm_passed(
            arm, treatment=False, artifact_changed=True,
        ))
        self.assertFalse(runner._arm_passed(
            arm, treatment=True, artifact_changed=True,
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
