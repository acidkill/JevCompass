"""Deterministic activation-contract pilot for advisor hook events."""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest import mock

from jevcompass import advisor  # noqa: E402


FIXTURE = Path(__file__).parent / "fixtures" / "advisor_pilot.json"
CANDIDATES = [
    {"id": "serena", "kind": "tool", "capability": "Symbol navigation", "use_when": "code references", "avoid_when": "trivial text"},
    {"id": "exec_command", "kind": "tool", "capability": "Local checks", "use_when": "run tests", "avoid_when": "unclear mutation"},
    {"id": "create-plan", "kind": "skill", "capability": "Plan complex work", "use_when": "multi-step plans", "avoid_when": "trivia"},
]


class AdvisorPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(FIXTURE.read_text())

    def test_fixture_has_exactly_twenty_named_cases(self):
        self.assertEqual(len(self.cases), 20)
        self.assertEqual(len({case["name"] for case in self.cases}), 20)
        self.assertEqual(sum(case["event"]["hook_event_name"] == "SubagentStart" and case["active"] for case in self.cases), 3)
        self.assertEqual(sum(case["event"]["hook_event_name"] == "UserPromptSubmit" and case["active"] for case in self.cases), 8)
        self.assertEqual(sum(not case["active"] for case in self.cases), 9)

    def test_activation_contract_is_deterministic(self):
        for case in self.cases:
            event = dict(case["event"])
            if "prompt_length" in case:
                event["prompt"] = "x" * case["prompt_length"]
            with self.subTest(case=case["name"]), \
                 mock.patch.object(advisor, "candidates", return_value=CANDIDATES) as candidates, \
                 mock.patch.object(advisor, "_read_cache", return_value=None), \
                 mock.patch.object(advisor, "_write_cache"), \
                 mock.patch.object(advisor, "_judge", return_value=["serena", "create-plan"]) as judge, \
                 mock.patch.object(advisor, "_metric"):
                output = advisor.evaluate(event)
                self.assertEqual(output is not None, case["active"])
                if case["active"]:
                    candidates.assert_called_once_with(task_kind=advisor.CATALOG_TASKS[case["category"]], role="any", domain=case["domain"], limit=20)
                    judge.assert_called_once()
                    hook_output = output["hookSpecificOutput"]
                    self.assertEqual(hook_output["hookEventName"], event["hook_event_name"])
                    self.assertIn("`serena`", hook_output["additionalContext"])
                    self.assertIn("`create-plan`", hook_output["additionalContext"])
                else:
                    candidates.assert_not_called()
                    judge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
