"""Supervisor advice placement, accounting and nonblocking failure evidence."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.test_pilot_test_order_pair import command_event

SPEC = importlib.util.spec_from_file_location(
    "pretask_pair", Path(__file__).parents[1] / "scripts" / "pilot_pretask_strategy_pair.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class PretaskPairTests(unittest.TestCase):
    def test_help_describes_supervisor_only_access(self):
        output = io.StringIO()
        with patch("sys.stdout", output), self.assertRaises(SystemExit) as stopped:
            runner.main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("supervisor-only API access", output.getvalue())
        self.assertNotIn("both isolated arms", output.getvalue())

    def test_supervisor_advice_precedes_launch_and_counts_preparation(self):
        selected = {"status": "remote-choice", "candidate_ids": list(runner.ACTIONS)}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "_prepare", return_value=(selected, {"status": "complete"})) as prepare, \
                    patch.object(runner.time, "monotonic", side_effect=[1, 1.5]), \
                    patch.object(runner, "_original_arm", return_value={
                        "completion_ms": 1000, "first_tool_start_ms": 100,
                        "first_successful_relevant_check_ms": 800,
                        "independent_quality_status": "frozen_checks_pass",
                    }) as launch:
                receipt = runner._run_arm(
                    prompt="private-prompt" + runner.MARKER,
                    home=Path(directory), allow_openrouter_key=True)
            prepare.assert_called_once_with(Path(directory), True)
            actual = launch.call_args.kwargs
            self.assertFalse(actual["allow_openrouter_key"])
            self.assertNotIn(runner.MARKER, actual["prompt"])
            self.assertNotIn("strategy choose", actual["prompt"])
            self.assertIn("Inspect existing symbol use before editing.", actual["prompt"])
            self.assertEqual(receipt["completion_ms"], 1500)
            self.assertEqual(receipt["first_tool_start_ms"], 600)
            self.assertEqual(receipt["first_successful_relevant_check_ms"], 1300)
            self.assertEqual(receipt["pretask_order_status"], "initial_prompt_before_agent_launch")
            self.assertNotIn("private-prompt", json.dumps(receipt))

    def test_baseline_never_prepares_advice_and_has_same_preflight(self):
        with patch.object(runner, "_prepare") as prepare, \
                patch.object(runner, "_original_arm", return_value={"completion_ms": 10}) as launch:
            receipt = runner._run_arm(prompt="task", home=Path("/unused"),
                                      allow_openrouter_key=False)
        prepare.assert_not_called()
        self.assertIn("JevCompass strategy receipt: none", launch.call_args.kwargs["prompt"])
        self.assertEqual(receipt["pretask_order_status"], "no_advice")

    def test_timeout_and_unknown_identifier_silently_skip(self):
        cases = [
            subprocess.TimeoutExpired(runner.COMMAND, 2),
            subprocess.CompletedProcess(runner.COMMAND, 1, stdout=b"private-output"),
            subprocess.CompletedProcess(runner.COMMAND, 0, stdout=b"{invalid-json"),
            subprocess.CompletedProcess(runner.COMMAND, 0, stdout=(
                b'{"status":"remote-choice","status":"no-remote-choice","strategies":[]}')),
            subprocess.CompletedProcess(runner.COMMAND, 0, stdout=json.dumps({
                "status": "remote-choice", "strategies": [
                    {"id": next(iter(runner.ACTIONS))},
                    {"id": next(iter(runner.ACTIONS))},
                ]}).encode()),
            subprocess.CompletedProcess(runner.COMMAND, 0, stdout=json.dumps({
                "status": "remote-choice", "strategies": [{"id": "private-backend-text"}]
            }).encode()),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for case in cases:
                options = {"side_effect": case} if isinstance(case, Exception) else {"return_value": case}
                with patch.object(runner.subprocess, "run", **options) as execute:
                    choice, usage = runner._prepare(Path(directory), False)
                self.assertEqual(choice["candidate_ids"], [])
                arguments = execute.call_args.args[0]
                self.assertEqual(arguments, runner.COMMAND)
                self.assertNotIn("OPENROUTER_API_KEY", execute.call_args.kwargs["env"])
                self.assertNotIn("private-backend-text", json.dumps((choice, usage)))

    def test_acknowledgment_order_and_relevant_check_timing(self):
        ids = tuple(runner.ACTIONS)
        message = json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": "JevCompass strategy receipt: " + ",".join(ids)}})
        started = command_event("item.started", "unit", runner.engine.UNIT_COMMAND)
        completed = command_event("item.completed", "unit", runner.engine.UNIT_COMMAND,
                                  exit_code=0, aggregated_output="Ran 3 tests in 0.001s\n\nOK\n")
        with patch.object(runner, "_expected_ids", ids):
            before = runner._event_receipts([message, started, completed], [1, 2, 3], 0)
            after = runner._event_receipts([started, message], [1, 2], 0)
        self.assertEqual(before["pretask_acknowledgment"], "before_first_tool")
        self.assertEqual(after["pretask_acknowledgment"], "after_first_tool")
        self.assertEqual(before["first_successful_relevant_check_ms"], 3000)
        self.assertNotIn("Ran 3 tests", json.dumps(before))

    def test_unmatched_test_diagnostics_do_not_waive_validation(self):
        command = "python3 -m unittest discover -s tests -p 'test_unit*.py' -v"
        completed = command_event("item.completed", "alias", command,
                                  exit_code=0, aggregated_output="private-log")
        routine = command_event("item.completed", "read", "cat /private/source",
                                exit_code=0, aggregated_output="private-code")
        receipt = runner._event_receipts([completed, completed, routine], [1, 2, 3], 0)
        self.assertEqual(receipt["unmatched_test_command_counts"], {"unittest": 1, "pytest": 0})
        self.assertEqual(receipt["focused_invocation_count"], 0)
        self.assertIsNone(receipt["first_successful_relevant_check_ms"])
        self.assertNotIn("private-", json.dumps(receipt))
        self.assertNotIn("/private", json.dumps(receipt))

    def test_known_checks_are_not_unmatched_and_instructions_are_explicit(self):
        event = command_event("item.completed", "known", runner.engine.UNIT_COMMAND,
                              exit_code=0)
        receipt = runner._event_receipts([event], [1], 0)
        self.assertEqual(receipt["unmatched_test_command_counts"], {"unittest": 0, "pytest": 0})
        self.assertIn(runner.engine.UNIT_COMMAND, runner.engine.BASE_PROMPT)
        self.assertIn(runner.engine.CONTRACT_COMMAND, runner.engine.BASE_PROMPT)
        self.assertIn("separate command", runner.engine.BASE_PROMPT)
