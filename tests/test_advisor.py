"""Synthetic contract tests for the two non-blocking advisory hooks."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jevcompass import advisor
from jevcompass.decisions import DecisionsError


ITEMS = [
    {"id": "serena", "kind": "tool", "capability": "Symbol navigation", "use_when": "code references", "avoid_when": "trivial text"},
    {"id": "shell", "kind": "tool", "capability": "Local checks", "use_when": "run tests", "avoid_when": "unclear mutation"},
    {"id": "create-plan", "kind": "skill", "capability": "Plan complex work", "use_when": "multi-step plans", "avoid_when": "trivia"},
    {"id": "python-packaging", "kind": "skill", "capability": "Package Python", "use_when": "Python packages", "avoid_when": "other work"},
]


def answers(*, answer="serena", skill="create-plan", confidence=0.9):
    return {
        "tool": {"type": "choice", "choice": answer, "confidence": 0.8},
        "skill": {"type": "choice", "choice": skill, "confidence": confidence},
    }


class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        for patcher in (
            mock.patch.object(advisor, "CACHE_DIR", base / "cache"),
            mock.patch.object(advisor, "LOG_PATH", base / "log.jsonl"),
            mock.patch.object(advisor, "candidates", return_value=ITEMS),
            mock.patch.object(advisor, "catalog_version", return_value="v-test"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_nonplan_prompt_is_quiet_and_does_not_call_jev(self):
        with mock.patch.object(advisor, "DecisionsClient") as client:
            for mode in ("default", "bypassPermissions", "acceptEdits"):
                self.assertIsNone(advisor.evaluate({"hook_event_name": "UserPromptSubmit", "permission_mode": mode, "prompt": "debug Python"}))
            client.assert_not_called()

    def test_diagnostic_marker_is_at_front_and_log_contains_no_prompt(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "debug Python private-client-secret-123"}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            output = advisor.evaluate(event, trace="0123abcd")
        self.assertTrue(output["hookSpecificOutput"]["additionalContext"].startswith("Diagnostic advice id: 0123abcd"))
        record = json.loads(advisor.LOG_PATH.read_text().splitlines()[-1])
        self.assertEqual(record["trace"], "0123abcd")
        self.assertNotIn("private-client-secret-123", advisor.LOG_PATH.read_text())

    def test_explicit_plan_entry_uses_only_allowlisted_metadata(self):
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            output = advisor.select_advice("UserPromptSubmit", "coding", "python", "planner")
        self.assertIn("`serena`", output["hookSpecificOutput"]["additionalContext"])
        request = client.return_value.decide.call_args.args[0]
        self.assertEqual(request["task_kind"], "coding")
        self.assertEqual(request["domain"], "python")

    def test_cache_key_changes_when_catalog_changes(self):
        first = advisor._cache_key("UserPromptSubmit", "coding", "planner", "python", ITEMS)
        with mock.patch.object(advisor, "catalog_version", return_value="another-version"):
            second = advisor._cache_key("UserPromptSubmit", "coding", "planner", "python", ITEMS)
        self.assertNotEqual(first, second)

    def test_invalid_confidence_is_skipped(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "debug Python"}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = {
                "tool": {"type": "choice", "choice": "serena", "confidence": 0.2},
                "skill": {"type": "choice", "choice": "create-plan", "confidence": 0.2},
            }
            self.assertIsNone(advisor.evaluate(event))

    def test_plan_uses_only_sanitized_categories_and_known_ids(self):
        secret = "private-client-secret-123"
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": f"Plan a Python debugging task for {secret}"}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            output = advisor.evaluate(event)
        request = client.return_value.decide.call_args.args[0]
        self.assertNotIn(secret, json.dumps(request))
        self.assertNotIn(secret, json.dumps(output))
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("primary agent", output["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("decision", output)

    def test_cache_avoids_second_jev_call(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "debug Python"}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            self.assertIsNotNone(advisor.evaluate(event))
            self.assertIsNotNone(advisor.evaluate(event))
            self.assertEqual(client.call_count, 1)

    def test_subagent_role_only_and_generic_role_skipped(self):
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            self.assertIsNone(advisor.evaluate({"hook_event_name": "SubagentStart", "agent_type": "default"}))
            self.assertIsNotNone(advisor.evaluate({"hook_event_name": "SubagentStart", "agent_type": "explorer"}))
            self.assertEqual(client.call_count, 1)

    def test_failures_are_silent_and_cannot_block(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "debug Python"}
        for effect in (DecisionsError("unavailable"), ValueError("invalid response")):
            with self.subTest(effect=effect), mock.patch.object(advisor, "DecisionsClient") as client:
                client.return_value.decide.side_effect = effect
                self.assertIsNone(advisor.evaluate(event))

    def test_duplicate_verdict_cannot_replace_missing_choice(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "debug Python"}
        duplicate = {"tool": {"type": "choice", "choice": "serena", "confidence": 0.8}}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = duplicate
            self.assertIsNone(advisor.evaluate(event))

    def test_unknown_task_or_missing_candidate_is_quiet(self):
        self.assertIsNone(advisor.evaluate({"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Hello"}))
        with mock.patch.object(advisor, "candidates", return_value=[]), mock.patch.object(advisor, "DecisionsClient") as client:
            self.assertIsNone(advisor.evaluate({"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "debug Python"}))
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
