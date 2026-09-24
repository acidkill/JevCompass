"""Synthetic contract tests for the two non-blocking advisory hooks."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jevcompass import advisor
from jevcompass.decisions import DecisionsError


ITEMS = [
    {"id": "serena", "kind": "tool", "capability": "Symbol navigation", "use_when": "code references", "avoid_when": "trivial text"},
    {"id": "exec_command", "kind": "tool", "capability": "Local checks", "use_when": "run tests", "avoid_when": "unclear mutation"},
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

    def test_substantive_task_is_advised_across_permission_modes(self):
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            for mode in ("default", "bypassPermissions", "acceptEdits", "plan"):
                output = advisor.evaluate({"hook_event_name": "UserPromptSubmit", "permission_mode": mode,
                                           "prompt": "Investigate a Python runtime error"})
                self.assertIn("`serena`", output["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(client.call_count, 1)
            self.assertEqual(client.return_value.decide.call_args.args[0]["role"], "primary")

    def test_vanilla_default_hook_sends_only_allowlisted_metadata(self):
        secret = "private-client-secret-123"
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "bypassPermissions",
                 "prompt": f"Implement a Python API with focused tests for {secret}"}
        output = io.StringIO()
        fake_stdin = mock.Mock(buffer=io.BytesIO(json.dumps(event).encode()))
        with mock.patch.object(advisor, "DecisionsClient") as client, \
                mock.patch.object(advisor.sys, "stdin", fake_stdin), mock.patch.object(advisor.sys, "stdout", output):
            client.return_value.decide.return_value = answers()
            self.assertEqual(advisor.hook_main(), 0)
        request = client.return_value.decide.call_args.args[0]
        context = json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(request["role"], "primary")
        self.assertEqual(request["task_kind"], "coding")
        self.assertNotIn(secret, json.dumps(request))
        self.assertNotIn(secret, context)
        self.assertNotIn(secret, advisor.LOG_PATH.read_text())
        self.assertTrue(context.startswith("JevCompass advice ID: "))

    def test_advice_id_is_at_front_and_log_contains_no_prompt(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Investigate a Python runtime error private-client-secret-123"}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            output = advisor.evaluate(event, trace="0123abcd")
        self.assertTrue(output["hookSpecificOutput"]["additionalContext"].startswith("JevCompass advice ID: 0123abcd"))
        record = json.loads(advisor.LOG_PATH.read_text().splitlines()[-1])
        self.assertEqual(record["trace"], "0123abcd")
        self.assertNotIn("private-client-secret-123", advisor.LOG_PATH.read_text())

    def test_hook_emits_unique_advice_id_on_cache_hit_and_logs_same_id(self):
        event = {"hook_event_name": "SubagentStart", "agent_type": "explorer"}
        contexts = []
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            for _ in range(2):
                output = io.StringIO()
                fake_stdin = mock.Mock(buffer=io.BytesIO(json.dumps(event).encode()))
                with mock.patch.object(advisor.sys, "stdin", fake_stdin), mock.patch.object(advisor.sys, "stdout", output):
                    self.assertEqual(advisor.hook_main(), 0)
                contexts.append(json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(client.call_count, 1)
        ids = [context.splitlines()[0].removeprefix("JevCompass advice ID: ") for context in contexts]
        self.assertTrue(all(len(identifier) == 8 for identifier in ids))
        self.assertNotEqual(ids[0], ids[1])
        records = [json.loads(line) for line in advisor.LOG_PATH.read_text().splitlines()]
        self.assertEqual([record["trace"] for record in records], ids)
        self.assertEqual([record["status"] for record in records], ["jev", "cache"])

    def test_hook_skips_normal_prompt_without_id_or_metric(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "bypassPermissions", "prompt": "git status"}
        output = io.StringIO()
        fake_stdin = mock.Mock(buffer=io.BytesIO(json.dumps(event).encode()))
        with mock.patch.object(advisor.sys, "stdin", fake_stdin), mock.patch.object(advisor.sys, "stdout", output), \
                mock.patch.object(advisor.secrets, "token_hex") as token, mock.patch.object(advisor, "DecisionsClient") as client:
            self.assertEqual(advisor.hook_main(), 0)
        self.assertEqual(output.getvalue(), "")
        token.assert_not_called()
        client.assert_not_called()
        self.assertFalse(advisor.LOG_PATH.exists())

    def test_short_and_single_step_requests_do_not_consult_jev(self):
        prompts = ("Run pytest", "Find tests that cover configuration parsing", "git status", "Hello")
        with mock.patch.object(advisor, "DecisionsClient") as client:
            for prompt in prompts:
                event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "bypassPermissions", "prompt": prompt}
                self.assertIsNone(advisor.evaluate(event), prompt)
            client.assert_not_called()

    def test_plan_review_explanation_and_project_setup_are_classified_locally(self):
        self.assertEqual(advisor.classify_task("Review the Python authentication changes and their callers"), ("review", "python"))
        self.assertEqual(
            advisor.classify_task("Inspect the Python authentication call graph and explain timeout behavior across the DecisionsClient, HTTP transport, and advisor fallback."),
            ("codebase", "python"),
        )
        self.assertEqual(advisor.classify_task("Prepare a task list to adapt a Python package"), ("planning", "python"))
        self.assertEqual(advisor.classify_task("Create a new private repository and Python package"), ("project-setup", "python"))
        self.assertEqual(advisor.classify_task("Review the new Python package API and its callers"), ("review", "python"))
        self.assertEqual(advisor.classify_task("Build documentation for the Python project"), ("documentation", "python"))
        self.assertIsNone(advisor.classify_task("Explain the timeout handling briefly"))

    def test_single_available_candidate_is_recommended_locally_without_jev(self):
        with mock.patch.object(advisor, "candidates", return_value=[ITEMS[1]]) as candidates, \
                mock.patch.object(advisor, "_judge") as judge, \
                mock.patch.object(advisor, "_metric") as metric:
            result = advisor.select_advice("UserPromptSubmit", "codebase", "software", "primary", "local1234")
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("exec_command", context)
        candidates.assert_called_once()
        judge.assert_not_called()
        metric.assert_called_once_with("UserPromptSubmit", "codebase", "local", mock.ANY, "local1234")

    def test_configured_only_mcp_is_excluded_from_automatic_advice(self):
        items = [
            {**ITEMS[0], "availability": "configured"},
            {**ITEMS[1], "availability": "available"},
            {"id": "git", "kind": "tool", "capability": "Repository checks", "use_when": "inspect status",
             "avoid_when": "no repository", "availability": "available"},
            {**ITEMS[2], "availability": "available"},
            {**ITEMS[3], "availability": "available"},
        ]
        with mock.patch.object(advisor, "candidates", return_value=items), mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers(answer="exec_command")
            output = advisor.evaluate({"hook_event_name": "UserPromptSubmit", "permission_mode": "default",
                                       "prompt": "Implement a Python API with focused tests"})
        context = output["hookSpecificOutput"]["additionalContext"]
        request = client.return_value.decide.call_args.args[0]
        self.assertNotIn("`serena`", context)
        self.assertNotIn("serena", json.dumps(request))
        self.assertIn("`exec_command`", context)

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
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Investigate a Python runtime error"}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = {
                "tool": {"type": "choice", "choice": "serena", "confidence": 0.2},
                "skill": {"type": "choice", "choice": "create-plan", "confidence": 0.2},
            }
            self.assertIsNone(advisor.evaluate(event))

    def test_task_uses_only_sanitized_categories_and_known_ids(self):
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
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Investigate a Python runtime error"}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            self.assertIsNotNone(advisor.evaluate(event))
            self.assertIsNotNone(advisor.evaluate(event))
            self.assertEqual(client.call_count, 1)

    def test_subagent_roles_use_only_supported_metadata_and_skip_custom_roles(self):
        private_transcript_path = "/private/transcripts/subagent-secret-7f39"
        private_project_path = "/private/worktrees/project-secret-1c24"
        vanilla_event = {
            "session_id": "session-secret-92b1",
            "turn_id": "turn-secret-831a",
            "transcript_path": private_transcript_path,
            "cwd": private_project_path,
            "hook_event_name": "SubagentStart",
            "model": "gpt-6-sol",
            "permission_mode": "default",
            "agent_id": "agent-secret-12ac",
        }
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            for agent_type in ("default", "luna_worker", "custom-auditor"):
                with self.subTest(agent_type=agent_type):
                    self.assertIsNone(advisor.evaluate({
                        **vanilla_event,
                        "agent_type": agent_type,
                    }))
            output = advisor.evaluate({
                **vanilla_event,
                "agent_type": "worker",
            })
            self.assertIsNotNone(output)
            self.assertEqual(client.call_count, 1)
            request = client.return_value.decide.call_args.args
            serialized_request = json.dumps(request)
            for private_value in (
                private_transcript_path,
                private_project_path,
                "session-secret-92b1",
                "turn-secret-831a",
                "agent-secret-12ac",
            ):
                self.assertNotIn(private_value, serialized_request)
                self.assertNotIn(private_value, output["hookSpecificOutput"]["additionalContext"])
            self.assertIn("worker", serialized_request)
            self.assertIn("coding", serialized_request)

    def test_failures_are_silent_and_cannot_block(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Investigate a Python runtime error"}
        for effect in (DecisionsError("unavailable"), ValueError("invalid response")):
            with self.subTest(effect=effect), mock.patch.object(advisor, "DecisionsClient") as client:
                client.return_value.decide.side_effect = effect
                self.assertIsNone(advisor.evaluate(event))

    def test_duplicate_verdict_cannot_replace_missing_choice(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Investigate a Python runtime error"}
        duplicate = {"tool": {"type": "choice", "choice": "serena", "confidence": 0.8}}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = duplicate
            self.assertIsNone(advisor.evaluate(event))

    def test_unknown_task_or_missing_candidate_is_quiet(self):
        self.assertIsNone(advisor.evaluate({"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Hello"}))
        with mock.patch.object(advisor, "candidates", return_value=[]), mock.patch.object(advisor, "DecisionsClient") as client:
            self.assertIsNone(advisor.evaluate({"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Investigate a Python runtime error"}))
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
