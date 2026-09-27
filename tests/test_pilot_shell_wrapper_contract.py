from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_test_order_pair.py"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("pilot_test_order_pair_shell_contract", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


def event(event_type: str, identifier: str, command: str, **extra) -> str:
    return json.dumps({
        "type": event_type,
        "item": {
            "id": identifier,
            "type": "command_execution",
            "command": command,
            **extra,
        },
    })


class ShellWrapperContractTests(unittest.TestCase):
    def test_observed_bash_and_sh_wrappers_match_focused_full_and_rank(self):
        focused = "/usr/bin/bash -lc " + json.dumps(runner.UNIT_COMMAND)
        full = "sh -c " + json.dumps(runner.REQUIRED_COMMAND)
        rank_command = (
            "python -m jevcompass tests rank --input test-options.json --json"
        )
        rank = "bash -lc " + json.dumps(rank_command)

        self.assertEqual(runner._test_kind({"command": focused}), "unit")
        self.assertEqual(runner._test_kind({"command": full}), "required")
        self.assertTrue(runner._rank_invocation({"command": rank}))

    def test_wrapped_evidence_is_correlated_to_the_command_event(self):
        focused = "/usr/bin/bash -lc " + json.dumps(runner.CONTRACT_COMMAND)
        full = "/usr/bin/bash -lc " + json.dumps(runner.REQUIRED_COMMAND)
        started = 100.0
        lines = [
            event("item.started", "focused-1", focused),
            event("item.completed", "focused-1", focused, exit_code=0),
            event("item.started", "full-1", full),
            event("item.completed", "full-1", full, exit_code=0),
        ]

        receipt = runner._event_receipts(
            lines, [started, started + 0.25, started + 0.5, started + 0.75], started
        )

        self.assertEqual(
            receipt["focused_test_exits"],
            [{"candidate_id": "contract", "exit_code": 0}],
        )
        self.assertEqual(receipt["required_suite_exit"], 0)
        self.assertTrue(receipt["required_suite_invocation_observed"])
        self.assertEqual(receipt["focused_invocation_count"], 1)

    def test_simple_quoted_arguments_are_preserved_without_execution(self):
        script = "python -m unittest -p 'test_unit*.py' -v"
        wrapped = "bash -c " + json.dumps(script)

        self.assertEqual(
            runner._command_argv({"command": wrapped}),
            ["python", "-m", "unittest", "-p", "test_unit*.py", "-v"],
        )

    def test_direct_argv_behavior_is_unchanged(self):
        direct = ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]

        self.assertEqual(runner._command_argv({"command": direct}), direct)

    def test_compound_or_unsafe_shell_syntax_is_rejected(self):
        scripts = [
            runner.UNIT_COMMAND + " && echo done",
            runner.UNIT_COMMAND + " | tee output.txt",
            runner.UNIT_COMMAND + " > output.txt",
            runner.UNIT_COMMAND + "; echo done",
            runner.REQUIRED_COMMAND + "\n echo done",
            runner.UNIT_COMMAND + "\n" + runner.CONTRACT_COMMAND,
            "python -m unittest $(echo test)",
            "python -m unittest $TEST_TARGET",
            "python -m unittest `echo test`",
            "PYTHONPATH=src " + runner.UNIT_COMMAND,
            "python -m unittest -p 'unterminated",
        ]
        for script in scripts:
            with self.subTest(script=script):
                command = "bash -lc " + json.dumps(script)
                self.assertIsNone(runner._command_argv({"command": command}))
                self.assertIsNone(runner._test_kind({"command": command}))

        for command in (
            "bash -lc " + json.dumps(runner.UNIT_COMMAND) + " extra",
            "bash --noprofile -lc " + json.dumps(runner.UNIT_COMMAND),
            "zsh -c " + json.dumps(runner.UNIT_COMMAND),
        ):
            with self.subTest(command=command):
                self.assertIsNone(runner._command_argv({"command": command}))

    def test_compound_process_exit_is_not_assigned_to_individual_tests(self):
        command = "bash -lc " + json.dumps(runner.UNIT_COMMAND + " && echo done")
        started = 100.0
        lines = [
            event("item.started", "compound", command),
            event("item.completed", "compound", command, exit_code=1),
        ]

        receipt = runner._event_receipts(lines, [started, started + 0.2], started)

        self.assertEqual(receipt["focused_test_exits"], [])
        self.assertEqual(receipt["required_suite_exit"], None)
        self.assertEqual(receipt["focused_invocation_count"], 0)
        self.assertFalse(receipt["required_suite_invocation_observed"])


if __name__ == "__main__":
    unittest.main()
