"""Offline tests for the bounded optional timeout-triage pair."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_ambiguous_timeout_pair.py"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("pilot_ambiguous_timeout_pair", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load timeout pair runner")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def event(kind, identifier, command=None, **extra):
    item = {"id": identifier, "type": "command_execution", **extra}
    if command is not None:
        item["command"] = command
    return json.dumps({"type": kind, "item": item})


def payload(status="no-remote-choice", ids=None, **extra):
    if ids is None:
        ids = list(runner.TIMEOUT_CANDIDATES)
    return json.dumps({
        "status": status,
        "steps": [{"id": i, "title": "local", "instruction": "check"} for i in ids],
        "observed_exit_status": 1,
        "test_failed": True,
        "executed": False,
        **extra,
    })


def events(*, call_triage=True, read_first=False, output=None):
    focus = list(shlex.split(runner.FOCUSED_COMMAND))
    full = list(shlex.split(runner.REQUIRED_COMMAND))
    values = [
        event("item.started", "focus1", focus),
        event("item.completed", "focus1", focus, exit_code=1, aggregated_output=output or (
            "ERROR: test_preloaded_item_is_available_without_waiting\n"
            "TimeoutError: inbox-not-ready"
        )),
    ]
    if read_first:
        values.append(event("item.started", "read1", ["cat", "inbox.py"]))
    if call_triage:
        triage = list(runner._triage_command(1))
        values.extend([
            event("item.started", "triage1", triage),
            event("item.completed", "triage1", triage, exit_code=0, aggregated_output=payload()),
        ])
    values.extend([
        event("item.started", "focus2", focus),
        event("item.completed", "focus2", focus, exit_code=0),
        event("item.started", "full1", full),
        event("item.completed", "full1", full, exit_code=0),
    ])
    return values


class AmbiguousTimeoutPairTests(unittest.TestCase):
    def test_command_uses_only_real_timeout_enums_without_observations(self):
        command = runner._triage_command(1)
        self.assertEqual(command, tuple(shlex.split(runner.TRIAGE_COMMAND_PREFIX)))
        from jevcompass.triage import HypothesisId
        self.assertEqual(
            set(runner.TIMEOUT_CANDIDATES),
            {HypothesisId.TIMEOUT_CONTENTION.value, HypothesisId.TIMEOUT_NONTERMINATING.value},
        )
        self.assertFalse(hasattr(HypothesisId, "TIMEOUT_SLOW_TEST"))
        self.assertFalse(any("observation" in arg for arg in command))
        with self.assertRaises(ValueError):
            runner._triage_command(0)

    def test_timeout_then_immediate_triage_validates_local_abstention(self):
        rows = events()
        started = 100.0
        result = runner._event_receipts(rows, [started + i / 10 for i in range(len(rows))], started)
        self.assertTrue(result["first_tool_was_focused"])
        self.assertEqual(result["initial_focused_exit"], 1)
        self.assertTrue(result["initial_timeout_error_observed"])
        self.assertIsNotNone(result["first_useful_error_ms"])
        self.assertTrue(result["triage_phase_proven"])
        self.assertEqual(result["triage_status"], "valid_local_abstention")
        self.assertEqual(result["triage_result"]["candidate_ids"], list(runner.TIMEOUT_CANDIDATES))
        self.assertEqual(result["triage_usage"]["status"], "not_reported")
        self.assertIsNone(result["triage_usage"]["jev_provider_cost_usd"])
        self.assertEqual(result["focused_rerun_exit"], 0)
        self.assertEqual(result["full_suite_exit"], 0)

    def test_treatment_only_after_actual_timeout_error(self):
        rows = events(output="ERROR: unrelated assertion")
        result = runner._event_receipts(rows, [float(i) for i in range(len(rows))], 0.0)
        self.assertEqual(result["initial_focused_exit"], 1)
        self.assertFalse(result["initial_timeout_error_observed"])
        self.assertFalse(result["triage_phase_proven"])
        self.assertEqual(result["triage_status"], "invalid_or_late")

    def test_local_inspection_before_triage_gets_no_phase_credit(self):
        rows = events(read_first=True)
        result = runner._event_receipts(rows, [float(i) for i in range(len(rows))], 0.0)
        self.assertTrue(result["initial_timeout_error_observed"])
        self.assertFalse(result["triage_phase_proven"])
        self.assertEqual(result["triage_status"], "invalid_or_late")

    def test_triage_before_focused_failure_is_rejected(self):
        triage = list(runner._triage_command(1))
        rows = [
            event("item.started", "triage0", triage),
            event("item.completed", "triage0", triage, exit_code=0, aggregated_output=payload()),
            *events(call_triage=False),
        ]
        result = runner._event_receipts(rows, [float(i) for i in range(len(rows))], 0.0)
        self.assertFalse(result["triage_phase_proven"])
        self.assertTrue(result["triage_invalid_invocation_observed"])

    def test_skipping_nonbinding_triage_does_not_fail_task_correctness(self):
        arm = {
            "cli_status": "completed", "first_tool_was_focused": True,
            "initial_focused_exit": 1, "initial_timeout_error_observed": True,
            "focused_rerun_exit": 0, "full_suite_exit": 0, "event_sequence_valid": True,
        }
        independent = {"focused": {"status": "passed"}, "full": {"status": "passed"}}
        self.assertTrue(runner._arm_task_correct(arm, independent, artifact_changed=True))

    def test_production_cli_accepts_selected_choice_followed_by_local_fallback(self):
        from jevcompass.cli import main
        output = io.StringIO()
        with mock.patch("jevcompass.triage.DecisionsClient") as client, contextlib.redirect_stdout(output):
            client.return_value.decide.return_value = {
                "diagnostic": {"type": "choice", "choice": "timeout_nonterminating", "confidence": 0.91}
            }
            exit_code = main([
                "triage", "--exit-code", "1", "--kind", "timeout",
                "--hypothesis", "timeout_contention",
                "--hypothesis", "timeout_nonterminating", "--json",
            ])
        self.assertEqual(exit_code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "remote-choice")
        self.assertEqual(
            [step["id"] for step in result["steps"]],
            ["timeout_nonterminating", "timeout_contention"],
        )
        self.assertEqual(
            runner._validated_triage(output.getvalue(), 1),
            {"status": "remote-choice",
             "candidate_ids": ["timeout_nonterminating", "timeout_contention"],
             "choice_kind": "validated_remote_choice"},
        )

    def test_production_cli_local_fallback_has_unknown_usage(self):
        from jevcompass.cli import main
        output = io.StringIO()
        with mock.patch("jevcompass.triage.DecisionsClient") as client, contextlib.redirect_stdout(output):
            client.return_value.decide.side_effect = RuntimeError("synthetic-transport-failure")
            exit_code = main([
                "triage", "--exit-code", "1", "--kind", "timeout",
                "--hypothesis", "timeout_contention",
                "--hypothesis", "timeout_nonterminating", "--json",
            ])
        result = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["status"], "no-remote-choice")
        self.assertEqual(runner._validated_triage(output.getvalue(), 1)["choice_kind"],
                         "valid_local_abstention")
        self.assertIsNone(result["decision_usage"])
        self.assertNotIn("synthetic-transport-failure", output.getvalue())

    def test_malformed_or_unrelated_triage_receipts_are_unscored(self):
        values = (
            payload(status="remote-choice", ids=[]),
            payload(status="remote-choice", ids=["timeout_contention", "timeout_contention"]),
            payload(status="remote-choice", ids=["private-path"]),
            payload(executed=True),
            payload(observed_exit_status=124),
            payload(test_failed=False),
        )
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(runner._validated_triage(value, 1)["status"], "unscored")

    def test_receipts_omit_raw_error_and_final_text(self):
        rows = events(output="PRIVATE_ERROR_BLOB\nTimeoutError: inbox-not-ready")
        rows.append(json.dumps({"type": "item.completed", "item": {
            "id": "final", "type": "agent_message", "text": "PRIVATE_FINAL_RESPONSE",
        }}))
        result = runner._event_receipts(rows, [float(i) for i in range(len(rows))], 0.0)
        text = json.dumps(result)
        for secret in ("PRIVATE_ERROR_BLOB", "PRIVATE_FINAL_RESPONSE", "inbox-not-ready"):
            self.assertNotIn(secret, text)

    def test_agent_environment_never_contains_advisor_key_or_network_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "PRIVATE_TEST_KEY"}):
                env = runner.core._isolated_environment(
                    home=Path(temporary) / "home",
                    isolated_python=Path(temporary) / "python",
                    allow_openrouter_key=False,
                )
                env.pop("OPENROUTER_API_KEY", None)
                command = runner.common._cli_command(
                    "codex", "gpt-6-luna", "low", "synthetic prompt",
                    allow_network=False,
                )
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertNotIn("PRIVATE_TEST_KEY", json.dumps(command))
        self.assertNotIn("sandbox_workspace_write.network_access=true", command)

    def test_changed_immutable_files_skip_independent_execution(self):
        with mock.patch.object(runner.timeout_fixture, "_independent_validation") as validate:
            result = runner._independent_after_frozen_inputs(
                Path("/tmp/unsafe-fixture"), Path("/tmp/home"), 1, False,
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["focused"]["status"], "skipped")
        self.assertEqual(result["full"]["status"], "skipped")
        validate.assert_not_called()

    def test_frozen_fixture_hash_and_exact_cli_versions(self):
        self.assertEqual(
            runner._verify_fixture(runner.FIXTURE),
            runner.timeout_fixture._fixture_digest(runner.FIXTURE),
        )
        self.assertEqual(runner.EXPECTED_CODEX_VERSION, "0.157.0")


if __name__ == "__main__":
    unittest.main()
