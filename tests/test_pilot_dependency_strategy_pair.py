"""Offline tests for the bounded dependency-guidance pair runner."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.test_pilot_test_order_pair import command_event

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dependency_strategy_pair", ROOT / "scripts" / "pilot_dependency_strategy_pair.py"
)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


CORRECT_ADAPTER = '''from collections.abc import Mapping


def load_document(client, document_key, stale_ok=False, cache=None):
    if cache is not None and not isinstance(cache, Mapping):
        raise TypeError("cache-must-be-a-mapping")
    cache_hint = None if cache is None else dict(cache)
    with client.open_document(
        document_key=document_key,
        allow_stale=stale_ok,
        cache_hint=cache_hint,
    ) as entry:
        payload = entry.payload
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise TypeError("payload-must-be-a-mapping-or-none")
    return dict(payload)
'''


class DependencyStrategyPairTests(unittest.TestCase):
    def copy_fixture(self, parent: Path, name: str = "fixture") -> Path:
        destination = parent / name
        shutil.copytree(runner.FIXTURE, destination)
        return destination

    def valid_output(self, **overrides) -> bytes:
        payload = {
            "status": "no-remote-choice",
            "strategies": [{
                "id": runner.EXPECTED_STRATEGY,
                "rationale": runner.EXPECTED_RATIONALE,
            }],
            "usage": None,
        }
        payload.update(overrides)
        return json.dumps(payload).encode("utf-8")

    def test_pair_fixture_prompt_and_rubric_are_frozen(self):
        with tempfile.TemporaryDirectory() as temporary:
            left = Path(temporary) / "left"
            right = Path(temporary) / "right"
            left_digest = runner.engine._copy_identical_fixture(runner.FIXTURE, left)
            right_digest = runner.engine._copy_identical_fixture(runner.FIXTURE, right)
        self.assertEqual(left_digest, right_digest)
        self.assertEqual(runner.engine.CHANGED_FILE, "ledger_adapter.py")
        self.assertEqual(runner.engine.FIXTURE, runner.FIXTURE)
        self.assertEqual(runner.engine.UNIT_COMMAND, runner.UNIT_COMMAND)
        self.assertEqual(runner.engine.REQUIRED_COMMAND, runner.REQUIRED_COMMAND)
        for text in (
            "API_CONTRACT.md",
            "Preserve",
            "Change only ledger_adapter.py",
            runner.UNIT_COMMAND,
            runner.REQUIRED_COMMAND,
        ):
            self.assertIn(text, runner.engine.BASE_PROMPT)
        self.assertEqual(
            runner.FROZEN_RUBRIC_SHA256,
            hashlib.sha256(runner.FROZEN_RUBRIC.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            runner.VERIFIER_SHA256,
            hashlib.sha256(runner.VERIFY_CONTRACT.read_bytes()).hexdigest(),
        )

    def test_preparation_uses_exact_offline_resolved_strategy_and_guidance(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with patch.object(
                runner.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    runner.STRATEGY_COMMAND, 0, stdout=self.valid_output()
                ),
            ) as execute:
                choice, usage = runner._prepare(home, runner.FIXTURE)

        call = execute.call_args
        self.assertEqual(call.args[0], runner.STRATEGY_COMMAND)
        self.assertEqual(
            call.args[0][call.args[0].index("--resolved-strategy") + 1],
            runner.EXPECTED_STRATEGY,
        )
        self.assertIn("dependency_change", call.args[0])
        self.assertIn("behavior_change", call.args[0])
        self.assertNotIn("--contract-evidence", call.args[0])
        self.assertFalse(call.kwargs["env"].get("OPENROUTER_API_KEY"))
        self.assertEqual(call.kwargs["timeout"], runner.PREPARATION_TIMEOUT_SECONDS)
        self.assertEqual(choice["status"], "no-remote-choice")
        self.assertEqual(choice["candidate_ids"], [runner.EXPECTED_STRATEGY])
        self.assertEqual(choice["rationale"], runner.EXPECTED_RATIONALE)
        self.assertIsNone(usage)

    def test_invalid_status_id_rationale_usage_or_duplicate_keys_fail_closed(self):
        cases = (
            self.valid_output(status="remote-choice"),
            self.valid_output(usage={"cost_usd": 0.01}),
            self.valid_output(strategies=[{
                "id": "define_contract_then_implement",
                "rationale": runner.EXPECTED_RATIONALE,
            }]),
            self.valid_output(strategies=[{
                "id": runner.EXPECTED_STRATEGY, "rationale": "unreviewed prose",
            }]),
            b'{"status":"no-remote-choice","status":"remote-choice",'
            b'"strategies":[],"usage":null}',
        )
        with tempfile.TemporaryDirectory() as temporary:
            for output in cases:
                with self.subTest(output=output[:60]), patch.object(
                    runner.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        runner.STRATEGY_COMMAND, 0, stdout=output
                    ),
                ):
                    choice, usage = runner._prepare(Path(temporary), runner.FIXTURE)
                self.assertEqual(choice["status"], "unscored")
                self.assertEqual(choice["candidate_ids"], [])
                self.assertIsNone(usage)

    def test_written_evidence_mismatch_skips_strategy_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.copy_fixture(Path(temporary))
            (fixture / "API_CONTRACT.md").write_text("changed", encoding="utf-8")
            with patch.object(runner.subprocess, "run") as execute:
                choice, usage = runner._prepare(Path(temporary), fixture)
        execute.assert_not_called()
        self.assertEqual(choice, {"status": "unscored", "candidate_ids": []})
        self.assertIsNone(usage)

    def test_appended_contract_contradiction_with_markers_skips_strategy_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.copy_fixture(Path(temporary))
            contract = fixture / "API_CONTRACT.md"
            original = contract.read_text(encoding="utf-8")
            contract.write_text(
                original + "\n\nContradiction: optional arguments must become keyword-only.\n",
                encoding="utf-8",
            )
            changed = contract.read_text(encoding="utf-8")
            self.assertTrue(all(marker in changed for marker in runner.CONTRACT_MARKERS))
            with patch.object(runner.subprocess, "run") as execute:
                choice, usage = runner._prepare(Path(temporary), fixture)
        execute.assert_not_called()
        self.assertEqual(choice, {"status": "unscored", "candidate_ids": []})
        self.assertIsNone(usage)

    def test_remote_provider_opt_in_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "remote provider opt-in"):
            runner.run_pair(allow_openrouter_key=True)

        output = io.StringIO()
        errors = io.StringIO()
        with patch("sys.stdout", output), patch("sys.stderr", errors), self.assertRaises(SystemExit) as stopped:
            runner.main([
                "--live", "--model", "gpt-6-luna", "--reasoning-effort", "low",
                "--output-dir", "/tmp/should-not-be-created", "--allow-openrouter-key",
            ])
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("unrecognized arguments: --allow-openrouter-key", errors.getvalue())
        self.assertEqual(output.getvalue(), "")

    def test_seeded_fixture_fails_and_correct_adapter_passes_ten_check_gate(self):
        initial = runner.frozen_files(runner.FIXTURE)
        seeded = runner._run_independent_gate(runner.FIXTURE, initial)
        self.assertTrue(seeded["immutable_files_preserved"])
        self.assertEqual(seeded["contract"]["status"], "failed")
        self.assertEqual(seeded["contract"]["tests_run"], 10)
        self.assertGreater(seeded["contract"]["failed"], 0)

        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.copy_fixture(Path(temporary))
            (fixture / "ledger_adapter.py").write_text(CORRECT_ADAPTER, encoding="utf-8")
            immutable = runner.frozen_files(fixture)
            passed = runner._run_independent_gate(fixture, immutable)
        self.assertTrue(passed["immutable_files_preserved"])
        self.assertEqual(passed["contract"]["status"], "passed")
        self.assertEqual(passed["contract"]["passed"], 10)
        self.assertEqual(passed["contract"]["failed"], 0)
        self.assertEqual(passed["contract"]["tests_run"], 10)

    def test_immutable_mutation_prevents_independent_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.copy_fixture(Path(temporary))
            immutable = runner.frozen_files(fixture)
            (fixture / "API_CONTRACT.md").write_text("tampered", encoding="utf-8")
            with patch.object(runner.subprocess, "run") as execute:
                gate = runner._run_independent_gate(fixture, immutable)
        execute.assert_not_called()
        self.assertFalse(gate["immutable_files_preserved"])
        self.assertNotEqual(
            gate["immutable_files_sha256_before"],
            gate["immutable_files_sha256_after"],
        )
        self.assertEqual(gate["contract"]["status"], "failed")
        self.assertIsNone(gate["independent_validation_ms"])

    def test_changed_verifier_source_hash_prevents_gate(self):
        immutable = runner.frozen_files(runner.FIXTURE)
        with patch.object(runner, "VERIFIER_SHA256", "0" * 64), patch.object(
            runner.subprocess, "run"
        ) as execute:
            gate = runner._run_independent_gate(runner.FIXTURE, immutable)
        execute.assert_not_called()
        self.assertFalse(gate["contract_verifier_unchanged"])
        self.assertEqual(gate["contract"]["status"], "failed")

    def test_gate_output_requires_exact_ten_successes_and_consistent_exit(self):
        valid = b'{"status":"passed","passed":10,"failed":0,"exit_code":0}\n'
        parsed = runner._parse_gate_output(valid, 0)
        self.assertEqual(parsed["tests_run"], 10)
        self.assertEqual(runner._parse_gate_output(valid, 1)["status"], "failed")
        for invalid in (
            b'{"status":"passed","passed":5,"failed":5,"exit_code":0}\n',
            b'{"status":"passed","passed":9,"failed":0,"exit_code":0}\n',
            b'{"status":"failed","passed":10,"failed":0,"exit_code":1}\n',
            b'{"status":"passed","passed":10,"failed":0,"exit_code":0,'
            b'"exit_code":0}\n',
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(runner._parse_gate_output(invalid, 0)["status"], "failed")

    def test_late_ack_is_recorded_and_duplicate_test_events_are_deduplicated(self):
        identifier = runner.EXPECTED_STRATEGY
        tool = json.dumps({
            "type": "item.started",
            "item": {"id": "tool-1", "type": "mcp_tool_call", "name": "serena.find_symbol"},
        })
        late_ack = json.dumps({
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "JevCompass dependency strategy receipt: " + identifier,
            },
        })
        events = [
            command_event("item.started", "focus", runner.UNIT_COMMAND),
            command_event("item.started", "focus", runner.UNIT_COMMAND),
            command_event("item.completed", "focus", runner.UNIT_COMMAND, exit_code=0),
            command_event("item.started", "full", runner.REQUIRED_COMMAND),
            command_event("item.completed", "full", runner.REQUIRED_COMMAND, exit_code=0),
            tool,
            late_ack,
        ]
        with patch.object(runner, "_expected_ids", (identifier,)):
            receipt = runner._event_receipts(events, [1, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6], 1.0)
        self.assertEqual(receipt["strategy_acknowledgment"], "after_first_tool")
        self.assertEqual(receipt["focused_command_count"], 1)
        self.assertEqual(receipt["required_command_count"], 1)
        self.assertEqual(receipt["test_command_order_status"], "focused_then_full")

    def good_gate(self):
        return {
            "immutable_files_preserved": True,
            "immutable_files_sha256_before": "a" * 64,
            "immutable_files_sha256_after": "a" * 64,
            "contract_verifier_sha256": runner.VERIFIER_SHA256,
            "contract_verifier_unchanged": True,
            "contract": {
                "status": "passed", "passed": 10, "failed": 0,
                "exit_code": 0, "tests_run": 10,
            },
            "independent_validation_ms": 25.0,
        }

    def event_set(self, ack_position="before"):
        acknowledgement = json.dumps({
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "JevCompass dependency strategy receipt: " + runner.EXPECTED_STRATEGY,
            },
        })
        tool = json.dumps({
            "type": "item.started",
            "item": {"id": "first-tool", "type": "mcp_tool_call", "name": "serena.find_symbol"},
        })
        events = [
            command_event("item.started", "focus", runner.UNIT_COMMAND),
            command_event("item.completed", "focus", runner.UNIT_COMMAND, exit_code=0),
            command_event("item.started", "full", runner.REQUIRED_COMMAND),
            command_event("item.completed", "full", runner.REQUIRED_COMMAND, exit_code=0),
        ]
        return [acknowledgement, tool, *events] if ack_position == "before" else [
            tool, acknowledgement, *events
        ]

    def test_validated_timing_includes_preparation_and_independent_gate_overhead(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.copy_fixture(Path(temporary))
            events = self.event_set("before")
            base = {
                "cli_status": "completed",
                "cli_exit_code": 0,
                "completion_ms": 1000.0,
                "required_suite_exit": 0,
                "focused_test_exits": [{"candidate_id": "unit", "exit_code": 0}],
                "first_useful_error_ms": 150.0,
            }

            def run_agent(**kwargs):
                result = dict(base)
                result.update(runner._event_receipts(
                    events, [2.0, 2.1, 2.2, 2.25, 2.3, 2.4], 2.0
                ))
                return result

            choice = {
                "status": "no-remote-choice",
                "candidate_ids": [runner.EXPECTED_STRATEGY],
                "rationale": runner.EXPECTED_RATIONALE,
            }
            with patch.object(runner, "_prepare", return_value=(choice, None)), patch.object(
                runner.time, "monotonic", side_effect=[1.0, 1.5]
            ), patch.object(runner, "_original_arm", side_effect=run_agent), patch.object(
                runner, "_run_independent_gate", return_value=self.good_gate()
            ):
                receipt = runner._run_arm(
                    prompt="frozen task" + runner.engine.TREATMENT_RANKING,
                    fixture=fixture,
                    home=Path(temporary),
                    timeout=30,
                    model="test-model",
                    reasoning_effort="low",
                    codex="codex",
                    allow_openrouter_key=True,
                )

        self.assertEqual(receipt["strategy_preparation_ms"], 500.0)
        self.assertEqual(receipt["agent_completion_ms"], 1000.0)
        self.assertEqual(receipt["completion_ms"], 1500.0)
        self.assertEqual(receipt["first_tool_start_ms"], 600.0)
        self.assertEqual(receipt["independent_validation_ms"], 25.0)
        self.assertEqual(receipt["validated_completion_ms"], 1525.0)
        self.assertEqual(receipt["strategy_acknowledgment"], "before_first_tool")
        self.assertEqual(receipt["arm_acceptance_status"], "passed")
        self.assertIsNone(receipt["first_useful_error_ms"])
        self.assertIsNone(receipt["strategy_usage"])
        self.assertIsNone(receipt["strategy_billing_estimate"])
        self.assertEqual(receipt["strategy_billing_status"], "unknown")
        self.assertEqual(receipt["billing_status"], "unknown")

    def test_missing_focused_failed_full_or_late_ack_withholds_completion(self):
        cases = (
            ("focused", {"focused_command_count": 0, "focused_test_exits": []}),
            ("full", {"required_suite_exit": 1}),
            ("late_ack", {"strategy_acknowledgment": "after_first_tool"}),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for index, (name, overrides) in enumerate(cases):
                fixture = self.copy_fixture(Path(temporary), f"fixture-{index}")
                events = self.event_set("before")
                base = {
                    "cli_status": "completed",
                    "cli_exit_code": 0,
                    "completion_ms": 100.0,
                    "required_suite_exit": 0,
                    "focused_test_exits": [{"candidate_id": "unit", "exit_code": 0}],
                }
                with self.subTest(case=name), patch.object(
                    runner, "_prepare", return_value=({
                        "status": "no-remote-choice",
                        "candidate_ids": [runner.EXPECTED_STRATEGY],
                        "rationale": runner.EXPECTED_RATIONALE,
                    }, None)
                ), patch.object(runner.time, "monotonic", side_effect=[1.0, 1.0]), patch.object(
                    runner, "_original_arm", return_value={
                        **base,
                        **runner._event_receipts(events, [2, 2.1, 2.2, 2.3, 2.4, 2.5], 2),
                        **overrides,
                    }
                ), patch.object(
                    runner, "_run_independent_gate", return_value=self.good_gate()
                ):
                    receipt = runner._run_arm(
                        prompt="task" + runner.engine.TREATMENT_RANKING,
                        fixture=fixture,
                        home=Path(temporary),
                    )
                self.assertEqual(receipt["arm_acceptance_status"], "failed")
                self.assertIsNone(receipt["validated_completion_ms"])

    def test_unscored_treatment_cannot_qualify_pair_timing(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.copy_fixture(Path(temporary))
            base = {
                "cli_status": "completed",
                "cli_exit_code": 0,
                "completion_ms": 100.0,
                "required_suite_exit": 0,
                "focused_test_exits": [{"candidate_id": "unit", "exit_code": 0}],
                **runner._event_receipts(
                    self.event_set(),
                    [2, 2.1, 2.2, 2.3, 2.4, 2.5],
                    2,
                ),
            }
            with patch.object(
                runner, "_prepare",
                return_value=({"status": "unscored", "candidate_ids": []}, None),
            ), patch.object(runner.time, "monotonic", side_effect=[1.0, 1.0]), patch.object(
                runner, "_original_arm", return_value=base
            ), patch.object(
                runner, "_run_independent_gate", return_value=self.good_gate()
            ):
                receipt = runner._run_arm(
                    prompt="task" + runner.engine.TREATMENT_RANKING,
                    fixture=fixture,
                    home=Path(temporary),
                )
        self.assertEqual(receipt["strategy_preparation_status"], "unscored")
        self.assertEqual(receipt["arm_acceptance_status"], "failed")
        self.assertIsNone(receipt["validated_completion_ms"])


if __name__ == "__main__":
    unittest.main()
