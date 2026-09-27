"""Offline contract-triage pair runner protocol and privacy tests."""
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
SCRIPT = ROOT / "scripts" / "pilot_contract_triage_pair.py"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("pilot_contract_triage_pair", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


def event(kind: str, event_id: str, command: str | None = None, **extra):
    item = {"id": event_id, "type": "command_execution", **extra}
    if command is not None:
        item["command"] = command
    return json.dumps({"type": kind, "item": item})


def arm_events(
    *, treatment: bool, include_evidence: bool = True, useful_failure: bool = True,
    include_triage: bool = True, acknowledgment: str = "before_first_tool",
    triage_acknowledgment: str = "before_next_tool", full_exit: int = 1,
):
    failure_output = "FAIL: test_invoice_total_matches_legacy_golden (test_invoice.InvoiceTotalTests)" + chr(10)
    failure_output += (
        "AssertionError: Decimal('0.02') != Decimal('0.01')"
        if useful_failure else "AssertionError: unexpected exception"
    )
    failure_output += chr(10) + "PRIVATE_DIAGNOSTIC_MARKER"
    lines = [
        event("item.started", "focused-1", runner.FOCUSED_COMMAND),
        event("item.completed", "focused-1", runner.FOCUSED_COMMAND,
              exit_code=1, aggregated_output=failure_output),
    ]
    if treatment and acknowledgment == "before_first_tool":
        lines.insert(0, json.dumps({
            "type": "item.completed", "item": {
                "id": "advice-ack", "type": "agent_message",
                "text": runner.WORKFLOW_ACK_LINE,
            },
        }))
    if treatment and include_evidence:
        evidence = (
            ("contract", "INVOICE_CONTRACT.md",
             "Round monetary amounts to cents using half-up rounding."),
            ("legacy", "legacy_golden.json",
             '{"line_amounts":["0.005","0.005"],"expected_total":"0.01"}'),
            ("implementation", "contractquote/totals.py",
             "def rounded_line_amounts(amounts): return [_round_to_cent(x) for x in amounts]; "
             "def invoice_total(amounts): return sum(rounded_line_amounts(amounts))"),
            ("test", "tests/test_invoice.py",
             "invoice_total(amounts); self.assertEqual(Decimal('0.01'), result)"),
        )
        command = "cat " + " ".join(filename for _kind, filename, _output in evidence)
        combined_output = "\\n".join(output for _kind, _filename, output in evidence)
        lines.extend([
            event("item.started", "evidence-all", command),
            event("item.completed", "evidence-all", command,
                  exit_code=0, aggregated_output=combined_output),
        ])
    if treatment and include_triage:
        triage = " ".join(runner._triage_argv(1))
        output = json.dumps({
            "observed_exit_status": 1,
            "test_failed": True,
            "status": "no-remote-choice",
            "steps": [{"id": "confirm_behavior_contract"}],
            "executed": False,
            "decision_usage": {
                "input_tokens": 17, "output_tokens": 9, "cost_usd": 0.003,
            },
        })
        lines.extend([
            event("item.started", "triage", triage),
            event("item.completed", "triage", triage, exit_code=0,
                  aggregated_output=output),
        ])
    if treatment and include_triage and triage_acknowledgment == "before_next_tool":
        lines.append(json.dumps({
            "type": "item.completed", "item": {
                "id": "triage-result-ack", "type": "agent_message",
                "text": runner.TRIAGE_RESULT_ACK_LINE,
            },
        }))
    lines.append(
        event("item.started", "full", runner.REQUIRED_COMMAND),
    )
    if treatment and include_triage and triage_acknowledgment == "after_next_tool":
        lines.append(json.dumps({
            "type": "item.completed", "item": {
                "id": "triage-result-ack", "type": "agent_message",
                "text": runner.TRIAGE_RESULT_ACK_LINE,
            },
        }))
    if treatment and acknowledgment == "after_first_tool":
        lines.append(json.dumps({
            "type": "item.completed", "item": {
                "id": "advice-ack", "type": "agent_message",
                "text": runner.WORKFLOW_ACK_LINE,
            },
        }))
    if treatment and acknowledgment == "invalid":
        lines.insert(0, json.dumps({
            "type": "item.completed", "item": {
                "id": "advice-ack", "type": "agent_message", "text": "Got it.",
            },
        }))
    lines.extend([
        event("item.completed", "full", runner.REQUIRED_COMMAND,
              exit_code=full_exit, aggregated_output="1 failing test\nPRIVATE_FULL_MARKER"),
        json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 34, "cached_input_tokens": 3,
            "cache_write_input_tokens": 0, "output_tokens": 11,
            "reasoning_output_tokens": 2,
        }}),
        json.dumps({"type": "item.completed", "item": {
            "id": "final", "type": "agent_message",
            "text": "PRIVATE_FINAL_MARKER: preserve failure; ask the policy owner.",
        }}),
    ])
    base = time.monotonic()
    return lines, [base + i * 0.001 for i in range(len(lines))], None


class FakeProcess:
    returncode = 0

    def __init__(self, prompt: str):
        self.prompt = prompt


class ContractTriagePairTests(unittest.TestCase):
    def _run_pair(
        self, temp: str, *, evidence: bool = True, failure: str | None = None,
        useful_failure: bool = True, mutate_legacy: bool = False,
        advice_policy: str = "legacy-required-step", include_triage: bool = True,
        acknowledgment: str = "before_first_tool",
        triage_acknowledgment: str = "before_next_tool", full_exit: int = 1,
    ):
        root = Path(temp)
        auth = root / "auth"
        auth.mkdir()
        (auth / "auth.json").write_text(
            '{"access_token":"AUTH_SECRET_MARKER"}', encoding="utf-8",
        )
        output = root / "private-receipts"
        calls = []

        def fake_popen(command, *, cwd=None, env=None, **kwargs):
            if mutate_legacy:
                legacy = Path(cwd) / "legacy_golden.json"
                legacy.write_text('{"expected_total":"0.99"}', encoding="utf-8")
            calls.append({
                "command": command, "cwd": cwd, "env": dict(env),
                "auth": (Path(env["CODEX_HOME"]) / "auth.json").read_bytes(),
            })
            return FakeProcess(command[-1])

        def collect(process, *, started, timeout, preserve_on_failure=False):
            treatment = process.prompt != runner.BASE_PROMPT
            lines, times, _ = arm_events(
                treatment=treatment, include_evidence=evidence,
                useful_failure=useful_failure,
                include_triage=include_triage,
                acknowledgment=acknowledgment,
                triage_acknowledgment=triage_acknowledgment,
                full_exit=full_exit,
            )
            return lines, times, failure

        with (
            mock.patch.dict(os.environ, {"CODEX_HOME": str(auth)}, clear=False),
            mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "OPENROUTER_SECRET"}, clear=False),
            mock.patch.object(runner, "_verify_codex_version", return_value=True),
            mock.patch.object(runner.subprocess, "Popen", side_effect=fake_popen),
            mock.patch.object(runner.core, "_collect_events", side_effect=collect),
        ):
            receipt = runner.run_pair(
                codex="/fake/codex", model="gpt-6-sol", reasoning_effort="high",
                timeout=3, seed=1, output_dir=output,
                advice_policy=advice_policy,
            )
        return receipt, calls, output

    def test_contract_pair_requires_evidence_before_enum_and_keeps_red_status(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt, calls, output = self._run_pair(temp)
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["original_failed_exit_preserved"], True)
            mapping = json.loads((output / "arm-map.json").read_text())
            treatment = receipt["arms"]["arm-a" if mapping["arm-a"] == "treatment" else "arm-b"]
            self.assertEqual(treatment["initial_focused_exit"], 1)
            self.assertEqual(treatment["full_suite_exit"], 1)
            self.assertEqual(treatment["triage_after_evidence"], True)
            self.assertEqual(treatment["triage"]["candidate_ids"], ["confirm_behavior_contract"])
            self.assertEqual(treatment["triage_usage"]["input_tokens"], 17)
            self.assertEqual(treatment["policy_check_timing_status"], "unscored")
            self.assertEqual(receipt["source_unchanged"], {"arm-a": True, "arm-b": True})
            self.assertEqual(receipt["tests_unchanged"], {"arm-a": True, "arm-b": True})
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["auth"], calls[1]["auth"])
            for call in calls:
                command = call["command"]
                self.assertEqual(command[command.index("--model") + 1], "gpt-6-sol")
                self.assertIn("model_reasoning_effort=high", command)
                self.assertNotIn("OPENROUTER_API_KEY", call["env"])
                self.assertNotIn("OPENROUTER_SECRET", repr(call["env"]))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            for path in output.iterdir():
                if path.is_file():
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            receipt_text = (output / "receipt.json").read_text()
            for marker in (
                "AUTH_SECRET_MARKER", "OPENROUTER_SECRET", "PRIVATE_DIAGNOSTIC_MARKER",
                "PRIVATE_FULL_MARKER", "PRIVATE_FINAL_MARKER", runner.TREATMENT_PROMPT,
                "INVOICE_CONTRACT.md",
            ):
                self.assertNotIn(marker, receipt_text)
            self.assertIn("PRIVATE_FINAL_MARKER", (output / "arm-a-final.txt").read_text()
                          + (output / "arm-b-final.txt").read_text())
            self.assertTrue((output / "arm-a-source.py").is_file())
            self.assertTrue((output / "arm-b-source.py").is_file())

    def test_treatment_triage_without_all_verified_reads_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt, _calls, _output = self._run_pair(temp, evidence=False)
            self.assertEqual(receipt["status"], "incomplete")
            # Resolve the randomized arm directly from the private map for assertions.
            with open(Path(temp) / "private-receipts" / "arm-map.json", encoding="utf-8") as handle:
                mapping = json.load(handle)
            treatment = receipt["arms"]["arm-a" if mapping["arm-a"] == "treatment" else "arm-b"]
            self.assertEqual(treatment["triage_invalid_invocation_observed"], True)
            self.assertEqual(treatment["triage_output_status"], "out_of_order_or_unverified")
            self.assertEqual(treatment["triage"]["status"], "unscored")

    def test_unrelated_focused_failure_does_not_satisfy_useful_failure_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt, _calls, _output = self._run_pair(temp, useful_failure=False)
            self.assertEqual(receipt["status"], "incomplete")
            self.assertTrue(all(
                arm["first_useful_failure_observed"] is False
                for arm in receipt["arms"].values()
            ))

    def test_legacy_golden_edit_fails_whole_fixture_unchanged_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt, _calls, _output = self._run_pair(temp, mutate_legacy=True)
            self.assertEqual(receipt["status"], "incomplete")
            self.assertEqual(receipt["fixture_unchanged_excluding_bytecode"],
                             {"arm-a": False, "arm-b": False})

    def test_timeout_is_recorded_as_runner_failure_not_a_test_result(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt, _calls, _output = self._run_pair(temp, failure="timeout")
            self.assertEqual(receipt["status"], "incomplete")
            self.assertTrue(all(
                arm["failure"] == "timeout" for arm in receipt["arms"].values()
            ))
            self.assertTrue(all(
                arm["initial_focused_exit"] is None for arm in receipt["arms"].values()
            ))

    def test_version_and_timeout_gates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture"
            source.mkdir()
            output = root / "out"
            with mock.patch.object(runner, "_verify_codex_version", return_value=False):
                with self.assertRaisesRegex(ValueError, "0.157.0"):
                    runner.run_pair(
                        codex="codex", model="gpt-6-sol", reasoning_effort="high",
                        timeout=2, output_dir=output, fixture_source=source,
                    )
            self.assertFalse(output.exists())
            with self.assertRaises(ValueError):
                runner.run_pair(
                    codex="codex", model="gpt-6-sol", reasoning_effort="high",
                    timeout=runner.MAX_TIMEOUT + 1, output_dir=output,
                    fixture_source=source,
                )

    def test_nonbinding_valid_local_abstention_is_correct_without_adoption(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt, _calls, _output = self._run_pair(
                temp, advice_policy="nonbinding", include_triage=False,
            )
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["task_correctness_status"], "passed")
            self.assertEqual(receipt["protocol_delivery_status"], "abstained")
            self.assertEqual(receipt["adoption_status"], "not_applicable")
            self.assertEqual(receipt["effect_scope"], "local_abstention")

    def test_nonbinding_local_triage_delivery_is_separately_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt, _calls, _output = self._run_pair(
                temp, advice_policy="nonbinding", include_triage=True,
            )
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["task_correctness_status"], "passed")
            self.assertEqual(receipt["protocol_delivery_status"], "advice_delivered")
            self.assertEqual(receipt["adoption_status"], "unscored")
            self.assertEqual(receipt["effect_scope"], "local_triage_advice")
            self.assertEqual(receipt["decision_scope"],
                             "local_fixture_triage_no_remote_choice")

    def test_nonbinding_triage_needs_ack_after_output_before_next_tool(self):
        for acknowledgment in ("not_observed", "after_next_tool", "invalid"):
            with self.subTest(acknowledgment=acknowledgment), tempfile.TemporaryDirectory() as temp:
                receipt, _calls, _output = self._run_pair(
                    temp, advice_policy="nonbinding", include_triage=True,
                    triage_acknowledgment=acknowledgment,
                )
                self.assertEqual(receipt["status"], "incomplete")
                self.assertEqual(receipt["task_correctness_status"], "passed")
                self.assertEqual(receipt["protocol_delivery_status"], "delivery_failed")
                self.assertEqual(receipt["adoption_status"], "unscored")

    def test_nonbinding_missing_or_late_workflow_ack_fails_delivery_not_correctness(self):
        for acknowledgment in ("not_observed", "after_first_tool", "invalid"):
            with self.subTest(acknowledgment=acknowledgment), tempfile.TemporaryDirectory() as temp:
                receipt, _calls, _output = self._run_pair(
                    temp, advice_policy="nonbinding", include_triage=False,
                    acknowledgment=acknowledgment,
                )
                self.assertEqual(receipt["status"], "incomplete")
                self.assertEqual(receipt["task_correctness_status"], "passed")
                self.assertEqual(receipt["protocol_delivery_status"], "delivery_failed")
                self.assertEqual(receipt["adoption_status"], "not_applicable")

    def test_nonbinding_still_fails_mandatory_full_suite_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt, _calls, _output = self._run_pair(
                temp, advice_policy="nonbinding", include_triage=False, full_exit=0,
            )
            self.assertEqual(receipt["status"], "incomplete")
            self.assertEqual(receipt["task_correctness_status"], "failed")
            self.assertEqual(receipt["protocol_delivery_status"], "abstained")

    def test_advice_policy_is_bounded_and_legacy_receipt_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt, _calls, _output = self._run_pair(temp)
            self.assertNotIn("advice_policy", receipt)
            self.assertNotIn("task_correctness_status", receipt)
            self.assertNotIn("adoption_status", receipt)
        for policy in (None, ["nonbinding"], "always-apply"):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as temp:
                with self.assertRaisesRegex(ValueError, "invalid advice policy"):
                    runner.run_pair(
                        codex="codex", model="gpt-6-sol", reasoning_effort="high",
                        timeout=3, output_dir=Path(temp) / "out",
                        advice_policy=policy,
                    )

    def test_enum_command_rejects_zero_or_bool_and_matches_frozen_cli(self):
        self.assertEqual(
            runner._triage_argv(1),
            [
                "python", "-m", "jevcompass", "triage", "--exit-code", "1",
                "--kind", "assertion",
                "--hypothesis", "assertion_expectation_drift",
                "--hypothesis", "assertion_behavior_regression",
                "--hypothesis", "confirm_behavior_contract",
                "--assertion-observation", "contract_underspecified", "--json",
            ],
        )
        for invalid in (0, True, "1"):
            with self.assertRaises(ValueError):
                runner._triage_argv(invalid)


if __name__ == "__main__":
    unittest.main()
