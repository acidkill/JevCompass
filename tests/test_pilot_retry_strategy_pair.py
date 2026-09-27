"""Offline runner checks for the local retry-contract strategy pair."""
from __future__ import annotations

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
    "retry_strategy_pair", ROOT / "scripts" / "pilot_retry_strategy_pair.py"
)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)

CORRECT_RETRY_SOURCE = '''from dataclasses import dataclass, field


@dataclass
class Response:
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)


def request_with_retry(send, sleep, max_attempts=3, base_delay=0.25):
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    for attempt in range(max_attempts):
        try:
            response = send()
        except ConnectionError:
            if attempt + 1 == max_attempts:
                raise
            sleep(base_delay)
            continue
        retryable = response.status_code == 429 or 500 <= response.status_code < 600
        if not retryable:
            return response
        if attempt + 1 == max_attempts:
            return response
        delay = base_delay
        try:
            delay = float(response.headers["Retry-After"])
        except (KeyError, TypeError, ValueError):
            pass
        sleep(delay)
    raise AssertionError("unreachable")
'''


class RetryStrategyPairTests(unittest.TestCase):
    def test_pair_uses_identical_fixture_bytes_and_frozen_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            left = Path(temporary) / "left"
            right = Path(temporary) / "right"
            left_digest = runner.engine._copy_identical_fixture(runner.FIXTURE, left)
            right_digest = runner.engine._copy_identical_fixture(runner.FIXTURE, right)
            self.assertEqual(left_digest, right_digest)
            self.assertEqual(runner.engine.core.fixture_digest(left),
                             runner.engine.core.fixture_digest(right))
        prompt = runner.engine.BASE_PROMPT
        for required in (
            "RETRY_CONTRACT.md", "public Response and request_with_retry API",
            "Change only retry.py", runner.UNIT_COMMAND, runner.REQUIRED_COMMAND,
        ):
            self.assertIn(required, prompt)
        self.assertEqual(runner.engine.CHANGED_FILE, "retry.py")
        self.assertEqual(runner.FROZEN_RUBRIC_SHA256,
                         __import__("hashlib").sha256(
                             runner.FROZEN_RUBRIC.encode("utf-8")).hexdigest())

    def test_treatment_preparation_is_exact_local_contract_route(self):
        stdout = json.dumps({
            "status": "no-remote-choice",
            "strategies": [{"id": runner.STRATEGY_IDS[0], "rationale": "private"}],
            "usage": None,
        }).encode("utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess(
                runner.STRATEGY_COMMAND, 0, stdout=stdout
            )) as execute:
                choice, usage = runner._prepare(Path(temporary))
        call = execute.call_args
        self.assertEqual(call.args[0], runner.STRATEGY_COMMAND)
        self.assertIn("--contract-evidence", call.args[0])
        self.assertIn("consistent", call.args[0])
        self.assertNotIn("OPENROUTER_API_KEY", call.kwargs["env"])
        self.assertEqual(call.kwargs["timeout"], runner.PREPARATION_TIMEOUT_SECONDS)
        self.assertEqual(choice["candidate_ids"], [runner.STRATEGY_IDS[0]])
        self.assertIsNone(usage)

    def test_remote_choice_and_malformed_or_usage_bearing_json_are_rejected(self):
        cases = [
            json.dumps({"status": "remote-choice", "strategies": [
                {"id": runner.STRATEGY_IDS[0]}], "usage": None}),
            json.dumps({"status": "no-remote-choice", "strategies": [
                {"id": runner.STRATEGY_IDS[0]}], "usage": {"cost_usd": 1}}),
            '{"status":"no-remote-choice","status":"no-remote-choice",'
            '"strategies":[],"usage":null}',
        ]
        with tempfile.TemporaryDirectory() as temporary:
            for output in cases:
                completed = subprocess.CompletedProcess(
                    runner.STRATEGY_COMMAND, 0, stdout=output.encode("utf-8")
                )
                with patch.object(runner.subprocess, "run", return_value=completed):
                    choice, usage = runner._prepare(Path(temporary))
                self.assertEqual(choice["status"], "unscored")
                self.assertEqual(choice["candidate_ids"], [])
                self.assertIsNone(usage)

    def test_local_consistent_route_must_return_only_the_expected_strategy(self):
        output = json.dumps({
            "status": "no-remote-choice",
            "strategies": [{"id": runner.STRATEGY_IDS[1]}],
            "usage": None,
        }).encode("utf-8")
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            runner.subprocess, "run",
            return_value=subprocess.CompletedProcess(runner.STRATEGY_COMMAND, 0, stdout=output),
        ):
            choice, usage = runner._prepare(Path(temporary))
        self.assertEqual(choice, {"status": "unscored", "candidate_ids": []})
        self.assertIsNone(usage)

    def test_supervisor_rejects_remote_opt_in_option(self):
        output = io.StringIO()
        errors = io.StringIO()
        with patch("sys.stdout", output), patch("sys.stderr", errors), \
                self.assertRaises(SystemExit) as stopped:
            runner.main([
                "--live", "--model", "gpt-6-luna", "--reasoning-effort", "low",
                "--output-dir", "/tmp/not-created", "--allow-openrouter-key",
            ])
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("unrecognized arguments: --allow-openrouter-key", errors.getvalue())
        self.assertEqual(output.getvalue(), "")

    def test_original_fixture_fails_independent_contract_gate_with_ten_checks(self):
        immutable = runner.frozen_files(runner.FIXTURE)
        gate = runner._run_independent_gate(runner.FIXTURE, immutable)
        self.assertTrue(gate["immutable_files_preserved"])
        self.assertEqual(gate["contract"]["status"], "failed")
        self.assertEqual(gate["contract"]["tests_run"], runner.EXPECTED_CONTRACT_TESTS)
        self.assertGreater(gate["contract"]["failed"], 0)
        self.assertIsNotNone(gate["independent_validation_ms"])

    def test_correct_synthetic_implementation_passes_contract_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            fixture.mkdir()
            (fixture / "retry.py").write_text(CORRECT_RETRY_SOURCE, encoding="utf-8")
            immutable = runner.frozen_files(fixture)
            gate = runner._run_independent_gate(fixture, immutable)
        self.assertTrue(gate["immutable_files_preserved"])
        self.assertEqual(gate["contract"]["status"], "passed")
        self.assertEqual(gate["contract"]["tests_run"], 10)
        self.assertEqual(gate["contract"]["passed"], 10)
        self.assertEqual(gate["contract"]["failed"], 0)

    def test_immutable_mutation_blocks_contract_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            shutil.copytree(runner.FIXTURE, fixture)
            immutable = runner.frozen_files(fixture)
            (fixture / "RETRY_CONTRACT.md").write_text("tampered", encoding="utf-8")
            with patch.object(runner.subprocess, "run") as execute:
                gate = runner._run_independent_gate(fixture, immutable)
        execute.assert_not_called()
        self.assertFalse(gate["immutable_files_preserved"])
        self.assertNotEqual(gate["immutable_files_sha256_before"],
                            gate["immutable_files_sha256_after"])
        self.assertEqual(gate["contract"]["status"], "failed")
        self.assertIsNone(gate["independent_validation_ms"])

    def test_changed_verifier_source_blocks_contract_execution(self):
        immutable = runner.frozen_files(runner.FIXTURE)
        with patch.object(runner, "VERIFIER_SHA256", "0" * 64), \
                patch.object(runner.subprocess, "run") as execute:
            gate = runner._run_independent_gate(runner.FIXTURE, immutable)
        execute.assert_not_called()
        self.assertFalse(gate["contract_verifier_unchanged"])
        self.assertEqual(gate["contract"]["status"], "failed")

    def test_arm_fails_pair_gate_and_suppresses_useful_timing_on_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            shutil.copytree(runner.FIXTURE, fixture)
            def mutate(**kwargs):
                (fixture / "tests" / "test_retry.py").write_text("tampered", encoding="utf-8")
                return {"cli_status": "completed", "completion_ms": 50,
                        "first_useful_error_ms": 10, "first_tool_start_ms": 5}
            with patch.object(runner, "_original_arm", side_effect=mutate), \
                    patch.object(runner, "_prepare") as prepare:
                receipt = runner._run_arm(
                    prompt="task", fixture=fixture, home=Path(temporary),
                )
        prepare.assert_not_called()
        self.assertEqual(receipt["cli_status"], "failed")
        self.assertFalse(receipt["immutable_files_preserved"])
        self.assertIsNone(receipt["first_useful_error_ms"])
        self.assertIsNone(receipt["validated_completion_ms"])
        self.assertEqual(receipt["strategy_usage"], None)
        self.assertEqual(receipt["strategy_billing_status"], "unknown")

    def test_late_acknowledgment_is_recorded_after_first_tool(self):
        identifier = runner.STRATEGY_IDS[0]
        tool = json.dumps({"type": "item.started", "item": {
            "id": "first-tool", "type": "mcp_tool_call", "name": "serena.find"}})
        acknowledgement = json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": "JevCompass strategy receipt: " + identifier}})
        with patch.object(runner, "_expected_ids", (identifier,)):
            receipt = runner._event_receipts([tool, acknowledgement], [1.0, 1.1], 1.0)
        self.assertEqual(receipt["strategy_acknowledgment"], "after_first_tool")

    def test_duplicate_command_events_do_not_inflate_invocation_counts(self):
        events = [
            command_event("item.started", "focus", runner.UNIT_COMMAND),
            command_event("item.started", "focus", runner.UNIT_COMMAND),
            command_event("item.completed", "focus", runner.UNIT_COMMAND, exit_code=0),
            command_event("item.started", "full", runner.REQUIRED_COMMAND),
            command_event("item.completed", "full", runner.REQUIRED_COMMAND, exit_code=0),
        ]
        receipt = runner._event_receipts(events, [1, 1.1, 1.2, 1.3, 1.4], 1.0)
        self.assertEqual(receipt["focused_command_count"], 1)
        self.assertEqual(receipt["required_command_count"], 1)
        self.assertEqual(receipt["test_command_order_status"], "focused_then_full")

    def test_missing_test_evidence_cannot_receive_validated_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            shutil.copytree(runner.FIXTURE, fixture)
            passed_gate = {
                "immutable_files_preserved": True,
                "immutable_files_sha256_before": "a" * 64,
                "immutable_files_sha256_after": "a" * 64,
                "contract_verifier_sha256": runner.VERIFIER_SHA256,
                "contract_verifier_unchanged": True,
                "contract": {"status": "passed", "passed": 10, "failed": 0,
                             "exit_code": 0, "tests_run": 10},
                "independent_validation_ms": 20.0,
            }
            with patch.object(runner, "_original_arm", return_value={
                "cli_status": "completed", "cli_exit_code": 0, "completion_ms": 100,
                "required_suite_exit": None, "focused_test_exits": [],
                "strategy_acknowledgment": "not_observed",
                "focused_command_count": 0, "required_command_count": 0,
                "test_command_order_status": "invalid_or_unobserved",
            }), patch.object(runner, "_run_independent_gate", return_value=passed_gate):
                receipt = runner._run_arm(prompt="task", fixture=fixture,
                                          home=Path(temporary))
        self.assertEqual(receipt["arm_acceptance_status"], "failed")
        self.assertIsNone(receipt["validated_completion_ms"])

    def test_required_failure_late_ack_or_missing_focused_check_blocks_timing(self):
        good_gate = {
            "immutable_files_preserved": True,
            "immutable_files_sha256_before": "b" * 64,
            "immutable_files_sha256_after": "b" * 64,
            "contract_verifier_sha256": runner.VERIFIER_SHA256,
            "contract_verifier_unchanged": True,
            "contract": {"status": "passed", "passed": 10, "failed": 0,
                         "exit_code": 0, "tests_run": 10},
            "independent_validation_ms": 10.0,
        }
        base = {
            "cli_status": "completed", "cli_exit_code": 0, "completion_ms": 100,
            "required_suite_exit": 0,
            "focused_test_exits": [{"candidate_id": "unit", "exit_code": 0}],
            "strategy_acknowledgment": "before_first_tool",
            "focused_command_count": 1, "required_command_count": 1,
            "test_command_order_status": "focused_then_full",
        }
        cases = (
            {"required_suite_exit": 1},
            {"strategy_acknowledgment": "after_first_tool"},
            {"focused_test_exits": [], "focused_command_count": 0},
        )
        with tempfile.TemporaryDirectory() as temporary:
            for index, overrides in enumerate(cases):
                fixture = Path(temporary) / f"fixture-{index}"
                shutil.copytree(runner.FIXTURE, fixture)
                with self.subTest(overrides=overrides), \
                        patch.object(runner, "_original_arm", return_value={**base, **overrides}), \
                        patch.object(runner, "_run_independent_gate", return_value=good_gate):
                    receipt = runner._run_arm(
                        prompt="task", fixture=fixture, home=Path(temporary)
                    )
                self.assertEqual(receipt["arm_acceptance_status"], "failed")
                self.assertEqual(receipt["cli_status"], "failed")
                self.assertIsNone(receipt["validated_completion_ms"])

    def test_preparation_latency_and_first_tool_ack_are_separately_accounted(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            shutil.copytree(runner.FIXTURE, fixture)
            ids = (runner.STRATEGY_IDS[0],)
            base_receipt = {
                "completion_ms": 1000, "first_tool_start_ms": 100,
                "first_useful_error_ms": 200, "first_observed_focused_failure_ms": 300,
                "cli_status": "completed", "cli_exit_code": 0,
                "independent_quality_status": "frozen_checks_pass",
                "required_suite_exit": 0,
                "focused_test_exits": [{"candidate_id": "unit", "exit_code": 0}],
                "test_command_order_status": "focused_then_full",
            }
            events = [
                json.dumps({"type": "item.completed", "item": {
                    "type": "agent_message", "text": "JevCompass strategy receipt: " + ids[0]}}),
                json.dumps({"type": "item.started", "item": {
                    "id": "first-tool", "type": "mcp_tool_call", "name": "serena.find"}}),
                command_event("item.started", "focus", runner.UNIT_COMMAND),
                command_event("item.completed", "focus", runner.UNIT_COMMAND, exit_code=0),
                command_event("item.started", "full", runner.REQUIRED_COMMAND),
                command_event("item.completed", "full", runner.REQUIRED_COMMAND, exit_code=0),
            ]
            def arm(**kwargs):
                result = dict(base_receipt)
                result.update(runner._event_receipts(
                    events, [2.0, 2.1, 2.2, 2.25, 2.3, 2.4], 2.0
                ))
                return result
            with patch.object(runner, "_prepare", return_value=(
                {"status": "no-remote-choice", "candidate_ids": list(ids)}, None
            )), patch.object(runner.time, "monotonic", side_effect=[1.0, 1.5, 2.0, 2.1]), \
                    patch.object(runner, "_original_arm", side_effect=arm), \
                    patch.object(runner, "_run_independent_gate", return_value={
                        "immutable_files_preserved": True,
                        "immutable_files_sha256_before": "a" * 64,
                        "immutable_files_sha256_after": "a" * 64,
                        "contract_verifier_sha256": runner.VERIFIER_SHA256,
                        "contract_verifier_unchanged": True,
                        "contract": {"status": "passed", "passed": 10, "failed": 0,
                                     "exit_code": 0, "tests_run": 10},
                        "independent_validation_ms": 25.0,
                    }):
                receipt = runner._run_arm(
                    prompt="task" + runner.engine.TREATMENT_RANKING,
                    fixture=fixture, home=Path(temporary), allow_openrouter_key=True,
                )
            runner._expected_ids = ()
        self.assertEqual(receipt["strategy_preparation_ms"], 500)
        self.assertEqual(receipt["agent_completion_ms"], 1000)
        self.assertEqual(receipt["completion_ms"], 1500)
        self.assertEqual(receipt["first_tool_start_ms"], 600)
        self.assertIsNone(receipt["first_useful_error_ms"])
        self.assertEqual(receipt["independent_validation_ms"], 25.0)
        self.assertEqual(receipt["validated_completion_ms"], 1525)
        self.assertEqual(receipt["strategy_acknowledgment"], "before_first_tool")
        self.assertEqual(receipt["focused_command_count"], 1)
        self.assertEqual(receipt["required_command_count"], 1)
        self.assertEqual(receipt["test_command_order_status"], "focused_then_full")
        self.assertEqual(receipt["strategy_usage"], None)
        self.assertEqual(receipt["strategy_billing_status"], "unknown")

    def test_gate_output_requires_matching_exit_and_exact_count(self):
        valid = b'{"status":"passed","passed":10,"failed":0,"exit_code":0}\n'
        self.assertEqual(runner._parse_gate_output(valid, 0)["tests_run"], 10)
        self.assertEqual(runner._parse_gate_output(valid, 1)["status"], "failed")
        self.assertEqual(runner._parse_gate_output(
            b'{"status":"passed","passed":9,"failed":0,"exit_code":0}\n', 0
        )["status"], "failed")
        contradictory = b'{"status":"passed","passed":5,"failed":5,"exit_code":0}\n'
        self.assertEqual(runner._parse_gate_output(contradictory, 0)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
