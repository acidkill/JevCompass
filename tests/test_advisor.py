"""Synthetic contract tests for default and opt-in non-blocking advisory hooks."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
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

    def test_shell_command_is_not_presented_as_a_codex_tool(self):
        pytest_command = {
            "id": "pytest", "kind": "tool", "invocation": "shell_command",
            "capability": "Run Python tests", "availability": "available",
        }
        output = advisor._context("UserPromptSubmit", ["pytest"], [pytest_command], "12345678")
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("local command `pytest`", context)
        self.assertIn("run through `exec_command`; confirm it is available in this session", context)
        self.assertNotIn("tool `pytest`", context)

    def test_shell_test_command_and_executor_are_composed_without_jev(self):
        executor = {**ITEMS[1], "availability": "available"}
        command = {"id": "unittest", "kind": "tool", "invocation": "shell_command",
                   "capability": "Run Python unit tests", "availability": "available",
                   "use_when": "test Python changes", "avoid_when": "no Python tests"}
        with mock.patch.object(advisor, "candidates", return_value=[executor, command]), \
                mock.patch.object(advisor, "DecisionsClient") as client, \
                mock.patch.object(advisor, "_metric") as metric:
            output = advisor.select_advice("UserPromptSubmit", "testing", "python", "primary")
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("tool `exec_command`", context)
        self.assertIn("local command `unittest`", context)
        self.assertIn("run through `exec_command`", context)
        client.assert_not_called()
        metric.assert_called_once_with("UserPromptSubmit", "testing", "local", mock.ANY, None)

    def test_shell_command_stays_with_remote_skill_choice(self):
        executor = {**ITEMS[1], "availability": "available"}
        command = {"id": "unittest", "kind": "tool", "invocation": "shell_command",
                   "capability": "Run Python unit tests", "availability": "available",
                   "use_when": "test Python changes", "avoid_when": "no Python tests"}
        skills = [{**item, "availability": "available"} for item in ITEMS[2:]]
        with mock.patch.object(advisor, "candidates", return_value=[executor, command, *skills]), \
                mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = {
                "skill": {"type": "choice", "choice": "python-packaging", "confidence": 0.9},
            }
            output = advisor.select_advice("UserPromptSubmit", "testing", "python", "primary")
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("`exec_command`", context)
        self.assertIn("local command `unittest`", context)
        self.assertIn("skill `python-packaging`", context)
        self.assertNotIn("skill `create-plan`", context)
        questions = client.return_value.decide.call_args.args[1]
        self.assertEqual(set(questions), {"skill"})

    def test_skill_advice_exposes_scope_before_optional_read(self):
        item = {**ITEMS[2], "availability": "available"}
        output = advisor._context("UserPromptSubmit", [item["id"]], [item], "12345678", category="review")
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("skill `create-plan`: Plan complex work (use when: multi-step plans; skip when: trivia)", context)
        self.assertIn("Inspect task scope first. If a listed skill's use condition fits, read its SKILL.md before drafting or editing; otherwise skip it.", context)
        self.assertLessEqual(len(context), advisor.MAX_CONTEXT_CHARS)

        oversized = {**item, "use_when": "x" * 161}
        output = advisor._context("UserPromptSubmit", [item["id"]], [oversized], "12345678")
        self.assertNotIn("use when:", output["hookSpecificOutput"]["additionalContext"])

    def test_skill_scope_criteria_never_silence_bounded_advice(self):
        skills = [
            {**ITEMS[2], "id": f"skill-{index}", "capability": "Relevant guidance " + "x" * 65,
             "use_when": "u" * 140, "avoid_when": "a" * 140, "availability": "available"}
            for index in range(3)
        ]
        output = advisor._context("UserPromptSubmit", [item["id"] for item in skills], skills, "12345678")
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context), advisor.MAX_CONTEXT_CHARS)
        self.assertIn("skill `skill-2`", context)
        self.assertNotIn("use when:", context)

    def test_spawn_advice_classifies_local_message_without_disclosing_it(self):
        secret = "private-client-token-84913"
        event = {
            "hook_event_name": "PreToolUse", "tool_name": "collaborationspawn_agent",
            "tool_input": {"task_name": "review_auth", "agent_type": "explorer",
                           "message": f"Review the Python authentication change and report a focused test for {secret}."},
        }
        output = io.StringIO()
        fake_stdin = mock.Mock(buffer=io.BytesIO(json.dumps(event).encode()))
        with mock.patch.object(advisor, "DecisionsClient") as client, \
                mock.patch.object(advisor.sys, "stdin", fake_stdin), mock.patch.object(advisor.sys, "stdout", output):
            client.return_value.decide.return_value = answers()
            self.assertEqual(advisor.hook_main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertNotIn("permissionDecision", json.dumps(result))
        self.assertNotIn("updatedInput", json.dumps(result))
        request = json.dumps(client.return_value.decide.call_args.args)
        self.assertIn('"task_kind": "review"', request)
        self.assertIn('"role": "explorer"', request)
        self.assertNotIn(secret, request)
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn(secret, advisor.LOG_PATH.read_text())

    def test_spawn_advice_uses_descriptive_title_when_message_is_opaque(self):
        event = {"hook_event_name": "PreToolUse", "tool_name": "spawn_agent",
                 "tool_input": {"message": "gAAAAA" * 70,
                                "task_name": "Review Python authentication changes"}}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            output = advisor.evaluate(event)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertEqual(client.return_value.decide.call_args.args[0]["task_kind"], "review")

    def test_spawn_advice_classifies_snake_case_title_when_message_is_encoded(self):
        encoded = "gAAAAA.09_FernET-token-encoded-shape-1234567890" * 9
        event = {"hook_event_name": "PreToolUse", "tool_name": "collaborationspawn_agent",
                 "tool_input": {"message": encoded, "task_name": "review_python_correctness"}}
        self.assertEqual(advisor._spawn_intent(event), ("review", "python", "subagent"))
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            output = advisor.evaluate(event)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PreToolUse")
        self.assertNotIn(encoded, json.dumps(client.return_value.decide.call_args.args))
        self.assertNotIn(encoded, json.dumps(output))

    def test_spawn_advice_abstains_for_unknown_tools_or_insufficient_intent(self):
        cases = (
            {"tool_name": "Bash", "tool_input": {"message": "Review the Python authentication change and tests."}},
            {"tool_name": "spawn_agent", "tool_input": {"message": "gAAAAA" * 100, "task_name": "review"}},
            {"tool_name": "Agent", "tool_input": {"message": "check logs", "task_name": "x", "agent_type": "default"}},
            {"tool_name": "collaborationspawn_agent", "tool_input": {"message": "x" * 10_001}},
            {"tool_name": "spawn_agent", "tool_input": "not-an-object"},
        )
        with mock.patch.object(advisor, "DecisionsClient") as client:
            for item in cases:
                event = {"hook_event_name": "PreToolUse", **item}
                self.assertIsNone(advisor.evaluate(event), event["tool_name"])
            client.assert_not_called()

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
        self.assertRegex(record["profile"], r"^[0-9a-f]{16}$")
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

    def test_fix_keyword_does_not_match_fixture_in_routine_prompts(self):
        for prompt in (
            "Print the current branch name in the synthetic fixture.",
            "Count the top-level files in the synthetic fixture.",
        ):
            self.assertIsNone(advisor.classify_task(prompt), prompt)
        self.assertEqual(
            advisor.classify_task("Fix a Bash script defect and run a syntax check."),
            ("debugging", "shell"),
        )

    def test_classifier_prioritizes_primary_intent_and_handles_shell_failures(self):
        self.assertEqual(
            advisor.classify_task("Review the Python change and report any bugs or regressions you find."),
            ("review", "python"),
        )
        self.assertEqual(
            advisor.classify_task("Update the Python README from current CLI help and existing test behavior."),
            ("documentation", "python"),
        )
        self.assertEqual(
            advisor.classify_task("Fix the Bash script's unset-variable defect and run a syntax check."),
            ("debugging", "shell"),
        )

    def test_package_install_documentation_has_a_narrow_local_category(self):
        self.assertEqual(
            advisor.classify_task("Update the Python project README install instructions to match the current CLI help and existing test behavior."),
            ("package-docs", "python"),
        )
        self.assertEqual(
            advisor.classify_task("Write general documentation for a Python application."),
            ("documentation", "python"),
        )
        self.assertEqual(
            advisor.classify_task("Update the web README installation guide for browser setup and document supported browser behavior."),
            ("documentation", "web"),
        )

    def test_package_docs_guidance_is_local_and_prompt_is_not_sent_to_jev(self):
        secret = "private-package-path-and-token-91aa"
        prompt = ("Update the Python project README install instructions to match the current CLI help "
                  f"and existing test behavior for {secret}.")
        candidates = [
            {"id": "exec_command", "kind": "tool", "capability": "Bounded local shell commands",
             "use_when": "inspect project files and run checks", "avoid_when": "unclear mutations",
             "availability": "available"},
            {"id": "git", "kind": "tool", "capability": "Inspect repository changes and history",
             "use_when": "check tracked README and metadata changes", "avoid_when": "forceful history changes",
             "availability": "available"},
            {"id": "python-packaging", "kind": "skill", "capability": "Python packaging guidance",
             "use_when": "Python package distribution and install docs", "avoid_when": "generic documentation",
             "availability": "available"},
        ]
        with mock.patch.object(advisor, "candidates", return_value=candidates), \
                mock.patch.object(advisor, "_read_cache", return_value=None), \
                mock.patch.object(advisor, "_write_cache"), \
                mock.patch.object(advisor, "_metric"), \
                mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = {
                "tool": {"type": "choice", "choice": "exec_command", "confidence": 0.9},
            }
            result = advisor.evaluate({
                "hook_event_name": "UserPromptSubmit",
                "permission_mode": "default",
                "prompt": prompt,
            })
        request = json.dumps(client.return_value.decide.call_args.args)
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(advisor.classify_task(prompt), ("package-docs", "python"))
        self.assertNotIn(prompt, request)
        self.assertNotIn(secret, request)
        self.assertNotIn("verify project metadata", request)
        self.assertIn("check project metadata", context)
        self.assertIn("Preserve its exact supported CLI help invocation", context)
        self.assertIn("python -m package --help", context)
        self.assertIn("pre-existing test failures", context)

    def test_package_docs_single_shell_skips_but_specific_skill_survives(self):
        shell = {
            "id": "exec_command", "kind": "tool", "capability": "Bounded local shell commands",
            "use_when": "inspect project files and run checks", "avoid_when": "unclear mutations",
            "availability": "available",
        }
        skill = {
            "id": "python-packaging", "kind": "skill", "capability": "Python packaging guidance",
            "use_when": "Python package distribution and install docs",
            "avoid_when": "generic documentation", "availability": "available",
        }
        with mock.patch.object(advisor, "candidates", return_value=[shell]), \
                mock.patch.object(advisor, "_judge") as judge, \
                mock.patch.object(advisor, "_metric") as metric:
            self.assertIsNone(advisor.select_advice(
                "UserPromptSubmit", "package-docs", "python", "primary"
            ))
            self.assertIsNone(advisor.select_advice(
                "UserPromptSubmit", "documentation", "python", "primary"
            ))
        judge.assert_not_called()
        self.assertTrue(all(call.args[2] == "low-signal-skip" for call in metric.call_args_list))

        with mock.patch.object(advisor, "candidates", return_value=[shell, skill]), \
                mock.patch.object(advisor, "_read_cache", return_value=None), \
                mock.patch.object(advisor, "_judge", return_value=None), \
                mock.patch.object(advisor, "_metric"):
            result = advisor.select_advice("UserPromptSubmit", "package-docs", "python", "primary")
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("python-packaging", context)
        self.assertIn("Preserve its exact supported CLI help invocation", context)

    def test_plan_review_explanation_and_project_setup_are_classified_locally(self):
        self.assertEqual(advisor.classify_task("Review the Python authentication changes and their callers"), ("review", "python"))
        self.assertEqual(
            advisor.classify_task("Inspect the Python authentication call graph and explain timeout behavior across the DecisionsClient, HTTP transport, and advisor fallback."),
            ("codebase", "python"),
        )
        self.assertEqual(advisor.classify_task("Prepare a task list to adapt a Python package"), ("planning", "python"))
        self.assertEqual(
            advisor.classify_task("Prepare a task list for packaging this CLI. Then inspect the install flow and explain any gaps."),
            ("planning", "general"),
        )
        self.assertEqual(advisor.classify_task("Review the task list and inspect the proposed implementation."), ("review", "general"))
        self.assertEqual(advisor.classify_task("Inspect the Python authentication call graph and explain timeout behavior."), ("codebase", "python"))
        self.assertEqual(advisor.classify_task("Create a new private repository and Python package"), ("project-setup", "python"))
        self.assertEqual(advisor.classify_task("Create a new Python package project and add tests."), ("project-setup", "python"))
        self.assertEqual(advisor.classify_task("Create Python tests for this project and run them."), ("testing", "python"))
        self.assertEqual(advisor.classify_task("Create a feature in this project and implement it."), ("coding", "general"))
        self.assertEqual(advisor.classify_task("Review counter.py for correctness and security in this fixture."), ("review", "python"))
        self.assertEqual(advisor.classify_task("Review counter.pyc for correctness and security in this fixture."), ("review", "general"))
        self.assertEqual(advisor.classify_task("Review the new Python package API and its callers"), ("review", "python"))
        self.assertEqual(advisor.classify_task("Build documentation for the Python project"), ("documentation", "python"))
        self.assertIsNone(advisor.classify_task("Explain the timeout handling briefly"))

    def test_explicit_repository_history_is_distinct_from_general_code_explanation(self):
        history_prompts = (
            "Inspect the commit history of this repository to find when the authentication function was introduced.",
            "Review git blame for this module and identify who changed the retry logic.",
            "Przejrzyj historię zmian w repozytorium i ustal, kto zmienił tę funkcję, a następnie wskaż powiązany commit i wpływ na moduł.",
        )
        for prompt in history_prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(advisor.classify_task(prompt), ("history", "software"))
        self.assertEqual(advisor.classify_task(
            "Inspect the authentication function and explain how retries work in the module."
        ), ("codebase", "general"))
        self.assertNotEqual(advisor.classify_task(
            "Write documentation on the history of science for a museum."
        ), ("history", "software"))

    def test_investigation_of_code_flow_receives_codebase_advice(self):
        prompt = "Investigate how the authentication flow crosses modules and where token refresh state is mutated."
        self.assertEqual(advisor.classify_task(prompt), ("codebase", "general"))
        self.assertEqual(
            advisor.classify_task("Investigate a Python runtime error across the authentication modules."),
            ("debugging", "python"),
        )
        self.assertIsNone(advisor.classify_task("Investigate this briefly"))
        with mock.patch.object(advisor, "select_advice", return_value={"hookSpecificOutput": {"additionalContext": "test"}}) as select:
            output = advisor.evaluate({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
        self.assertIsNotNone(output)
        select.assert_called_once_with(
            "UserPromptSubmit", "codebase", "software", "primary", None, security_relevant=True
        )

    def test_proposal_review_uses_source_review_without_exposing_content(self):
        prompt = ("Review a synthetic client proposal draft against the repository map and canonical pricing table. "
                  "Flag missing facts, keep the draft unsent, and report its intended path and filename.")
        self.assertEqual(advisor.classify_task(prompt), ("source-review", "general"))
        self.assertEqual(
            advisor.classify_task("Review counter.py for a correctness issue and quote only the advice ID before tools."),
            ("review", "python"),
        )
        self.assertEqual(
            advisor.classify_task("Review the client price quote against the canonical pricing table and flag missing facts."),
            ("source-review", "general"),
        )
        self.assertEqual(
            advisor.classify_task(prompt + " State only the IDs visible in your initial context before using tools."),
            ("source-review", "general"),
        )
        self.assertEqual(
            advisor.classify_task("Initialize a new repository with package metadata and a smoke test."),
            ("project-setup", "software"),
        )
        self.assertEqual(
            advisor.classify_task("Run git init to create a new repository for the package and add its minimal metadata."),
            ("project-setup", "software"),
        )
        self.assertEqual(
            advisor.classify_task("Przejrzyj ofertę klienta względem aktualnego źródła cen, oznacz brakujące fakty i zachowaj jako niesłany szkic do weryfikacji."),
            ("source-review", "general"),
        )
        self.assertEqual(
            advisor.classify_task("Review the Python code that renders a client offer and report implementation defects."),
            ("review", "python"),
        )
        source_review_tool = {**ITEMS[1], "id": "local-source-review", "capability": "Review authoritative local sources"}
        with mock.patch.object(advisor, "candidates", return_value=[source_review_tool]) as candidates, \
                mock.patch.object(advisor, "_judge") as judge:
            output = advisor.evaluate({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
        context = output["hookSpecificOutput"]["additionalContext"]
        candidates.assert_called_once_with(task_kind="source-review", role="any", domain="general", limit=20)
        judge.assert_not_called()
        self.assertIn("current authoritative local sources", context)
        self.assertIn("mark missing facts", context)
        self.assertIn("Keep drafts unsent unless explicitly authorized", context)
        self.assertNotIn("client proposal draft", context)
        self.assertNotIn("pricing table", context)

    def test_codex_docs_and_troubleshooting_classify_narrowly(self):
        self.assertEqual(
            advisor.classify_task("Debug why Codex Desktop ignores the UserPromptSubmit hook after changing settings; explain the fix from the official docs."),
            ("debugging", "codex"),
        )
        self.assertEqual(
            advisor.classify_task("Update the documentation for Codex CLI skills setup and how CODEX_HOME discovers stock skills."),
            ("documentation", "codex"),
        )
        self.assertEqual(
            advisor.classify_task("Implement a Python API used alongside Codex and add focused request validation tests."),
            ("coding", "python"),
        )
        self.assertEqual(
            advisor.classify_task("Build a React web dashboard for Codex users and add tests for saved workspace preferences."),
            ("testing", "web"),
        )

    def test_codex_setup_recommends_installed_official_docs_only(self):
        prompt = "Configure Codex Desktop hooks for a new project using the supported settings."
        self.assertEqual(advisor.classify_task(prompt), ("codex-setup", "codex"))
        self.assertEqual(
            advisor.classify_task("Set up Codex CLI skills and settings for a new project."),
            ("codex-setup", "codex"),
        )
        self.assertNotEqual(
            advisor.classify_task("Set up a Python package for developers who use Codex CLI."),
            ("codex-setup", "codex"),
        )
        self.assertNotEqual(
            advisor.classify_task("Configure the React dashboard settings for this project."),
            ("codex-setup", "codex"),
        )
        docs = {"id": "openai-docs", "kind": "skill", "capability": "Official Codex documentation",
                "use_when": "Codex setup", "avoid_when": "unrelated coding", "availability": "available"}
        office_docs = {**docs, "id": "documents", "capability": "Create office documents"}
        with mock.patch.object(advisor, "candidates", return_value=[office_docs, docs]) as candidates, \
                mock.patch.object(advisor, "_judge") as judge:
            result = advisor.evaluate({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
        candidates.assert_called_once_with(task_kind="document", role="any", domain="codex", limit=20)
        judge.assert_not_called()
        self.assertIn("skill `openai-docs`", result["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("documents", result["hookSpecificOutput"]["additionalContext"])
        with mock.patch.object(advisor, "candidates", return_value=[]), \
                mock.patch.object(advisor, "_judge") as judge:
            self.assertIsNone(advisor.evaluate({"hook_event_name": "UserPromptSubmit", "prompt": prompt}))
        judge.assert_not_called()

    def test_supplemental_codex_setup_pair_routing(self):
        setup = ("Plan how to configure Codex Desktop hooks and Codex CLI skills for fictional OrbitNote "
                 "using only the synthetic repository files. Explain the two advisory triggers, install and trust "
                 "checks, and what remains unverified. Do not edit host files or contact network services; "
                 "cite the fixture files for each step.")
        routine = "Check whether docs/verification.md exists in the synthetic fixture."
        self.assertEqual(advisor.classify_task(setup), ("codex-setup", "codex"))
        self.assertIsNone(advisor.classify_task(routine))
        fixture = Path(__file__).parent / "fixtures" / "codex_setup"
        self.assertTrue((fixture / "README.md").is_file())
        self.assertTrue((fixture / "docs" / "hook-requirements.md").is_file())
        self.assertTrue((fixture / "docs" / "verification.md").is_file())

    def test_codex_troubleshooting_sends_only_allowlisted_metadata_to_jev(self):
        secret = "private-codex-config-value-7b31"
        prompt = ("Debug why Codex Desktop ignores the UserPromptSubmit hook after changing settings; "
                  f"explain the official documentation fix without exposing {secret}.")
        items = [*ITEMS, {
            "id": "openai-docs", "kind": "skill", "capability": "Official Codex and OpenAI documentation",
            "use_when": "Codex Desktop or CLI docs and troubleshooting", "avoid_when": "generic coding",
            "availability": "available",
        }]
        with mock.patch.object(advisor, "candidates", return_value=items), \
                mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            result = advisor.evaluate({"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": prompt})
        request = client.return_value.decide.call_args.args[0]
        self.assertEqual(request["task_kind"], "debugging")
        self.assertEqual(request["domain"], "codex")
        self.assertNotIn(prompt, json.dumps(request))
        self.assertNotIn(secret, json.dumps(request))
        self.assertNotIn(secret, result["hookSpecificOutput"]["additionalContext"])

    def test_single_specific_candidate_is_recommended_locally_without_jev(self):
        specific = {**ITEMS[1], "id": "specific-tool", "capability": "Specific repository inspection"}
        with mock.patch.object(advisor, "candidates", return_value=[specific]) as candidates, \
                mock.patch.object(advisor, "_judge") as judge, \
                mock.patch.object(advisor, "_metric") as metric:
            result = advisor.select_advice("UserPromptSubmit", "codebase", "software", "primary", "local1234")
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("specific-tool", context)
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

    def test_ambiguous_candidates_fall_back_to_unranked_local_shortlist_when_jev_fails(self):
        tools = [
            {"id": "exec_command", "kind": "tool", "capability": "Run local checks",
             "use_when": "inspect or test code", "avoid_when": "unreviewed destructive changes",
             "availability": "available"},
            {"id": "git", "kind": "tool", "capability": "Inspect repository state",
             "use_when": "check tracked changes", "avoid_when": "no repository",
             "availability": "available"},
        ]
        with mock.patch.object(advisor, "candidates", return_value=tools), \
                mock.patch.object(advisor, "DecisionsClient") as client, \
                mock.patch.object(advisor, "_metric") as metric:
            client.return_value.decide.side_effect = DecisionsError("no configured key")
            result = advisor.select_advice("UserPromptSubmit", "project-setup", "software", "primary")

        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Local unranked fallback; Jev did not select these candidates.", context)
        self.assertIn("`exec_command`", context)
        self.assertIn("`git`", context)
        self.assertNotIn("private-client-secret", context)
        self.assertNotIn("decision", result)
        self.assertNotIn("continue", result)
        metric.assert_called_once_with("UserPromptSubmit", "project-setup", "local", mock.ANY, None)

    def test_low_confidence_uses_local_fallback_instead_of_claiming_jev_selection(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Investigate a Python runtime error"}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = {
                "tool": {"type": "choice", "choice": "serena", "confidence": 0.2},
                "skill": {"type": "choice", "choice": "create-plan", "confidence": 0.2},
            }
            result = advisor.evaluate(event)

        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Local unranked fallback", context)
        for item in ITEMS:
            self.assertIn(item["id"], context)

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

    def test_cache_avoids_second_jev_call_and_keeps_jev_source_label(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Investigate a Python runtime error"}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = answers()
            first = advisor.evaluate(event)
            second = advisor.evaluate(event)
            self.assertEqual(client.call_count, 1)

        for result in (first, second):
            context = result["hookSpecificOutput"]["additionalContext"]
            self.assertNotIn("Local unranked fallback", context)
            self.assertIn("`serena`", context)

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

    def test_generic_shell_singleton_is_silent_in_hooks_without_calling_jev(self):
        shell = next(item for item in ITEMS if item["id"] == "exec_command")
        cases = (
            {"hook_event_name": "SubagentStart", "agent_type": "explorer"},
            {"hook_event_name": "UserPromptSubmit", "prompt": "Investigate a Python runtime error"},
        )
        for event in cases:
            with self.subTest(event=event), \
                    mock.patch.object(advisor, "candidates", return_value=[shell]), \
                    mock.patch.object(advisor, "DecisionsClient") as client:
                self.assertIsNone(advisor.evaluate(event, trace="abc12345"))
                client.assert_not_called()

        prompt_event = cases[1]
        output = io.StringIO()
        fake_stdin = mock.Mock(buffer=io.BytesIO(json.dumps(prompt_event).encode()))
        with mock.patch.object(advisor, "candidates", return_value=[shell]), \
                mock.patch.object(advisor, "DecisionsClient") as client, \
                mock.patch.object(advisor.sys, "stdin", fake_stdin), \
                mock.patch.object(advisor.sys, "stdout", output):
            self.assertEqual(advisor.hook_main(), 0)
            client.assert_not_called()
        self.assertEqual(output.getvalue(), "")

        record = json.loads(advisor.LOG_PATH.read_text().splitlines()[-1])
        self.assertEqual(record["status"], "low-signal-skip")

        specific = {**shell, "id": "specific-tool", "capability": "specific role tool"}
        with mock.patch.object(advisor, "candidates", return_value=[specific]), \
                mock.patch.object(advisor, "DecisionsClient") as client:
            specific_output = advisor.evaluate(prompt_event)
            client.assert_not_called()
        self.assertIn("specific-tool", specific_output["hookSpecificOutput"]["additionalContext"])

    def test_spawn_avoids_shell_and_git_only_advice_but_keeps_specific_candidates(self):
        shell = {**ITEMS[1], "availability": "available"}
        git = {"id": "git", "kind": "tool", "capability": "Inspect repository changes",
               "use_when": "review tracked changes", "avoid_when": "no repository",
               "availability": "available"}
        event = {"hook_event_name": "PreToolUse", "tool_name": "spawn_agent",
                 "tool_input": {"task_name": "review_python_correctness",
                                "message": "Review a fictional Python change and its required tests."}}
        with mock.patch.object(advisor, "candidates", return_value=[shell, git]), \
                mock.patch.object(advisor, "DecisionsClient") as client:
            self.assertIsNone(advisor.evaluate(event, trace="spawn123"))
            client.assert_not_called()
        record = json.loads(advisor.LOG_PATH.read_text().splitlines()[-1])
        self.assertEqual(record["status"], "low-signal-skip")
        self.assertEqual(record["category"], "review")

        skill = {**ITEMS[2], "availability": "available"}
        with mock.patch.object(advisor, "candidates", return_value=[shell, git, skill]), \
                mock.patch.object(advisor, "_judge", return_value=None):
            advised = advisor.evaluate(event)
        self.assertIn("`create-plan`", advised["hookSpecificOutput"]["additionalContext"])

        with mock.patch.object(advisor, "candidates", return_value=[shell, git]), \
                mock.patch.object(advisor, "_judge", return_value=None):
            prompt_advice = advisor.select_advice("UserPromptSubmit", "project-setup", "software", "primary")
        self.assertIn("`git`", prompt_advice["hookSpecificOutput"]["additionalContext"])

    def test_jev_failures_fall_back_locally_without_blocking(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Investigate a Python runtime error"}
        for effect in (DecisionsError("unavailable"), ValueError("invalid response")):
            with self.subTest(effect=effect), mock.patch.object(advisor, "DecisionsClient") as client:
                client.return_value.decide.side_effect = effect
                result = advisor.evaluate(event)

                context = result["hookSpecificOutput"]["additionalContext"]
                self.assertIn("Local unranked fallback", context)
                self.assertNotIn("decision", result)
                self.assertNotIn("continue", result)

    def test_incomplete_jev_answer_falls_back_locally(self):
        event = {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Investigate a Python runtime error"}
        duplicate = {"tool": {"type": "choice", "choice": "serena", "confidence": 0.8}}
        with mock.patch.object(advisor, "DecisionsClient") as client:
            client.return_value.decide.return_value = duplicate
            result = advisor.evaluate(event)

        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Local unranked fallback", context)
        for item in ITEMS:
            self.assertIn(item["id"], context)

    def test_hook_skip_does_not_import_catalog_or_decisions(self):
        root = Path(__file__).resolve().parents[1]
        script = """
import io
import json
import sys
from jevcompass import advisor
assert "jevcompass.catalog" not in sys.modules
assert "jevcompass.decisions" not in sys.modules
sys.stdin = type("Input", (), {"buffer": io.BytesIO(json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "Hello"}).encode())})()
assert advisor.hook_main() == 0
assert "jevcompass.catalog" not in sys.modules
assert "jevcompass.decisions" not in sys.modules
"""
        environment = os.environ.copy()
        source = str(root / "src")
        environment["PYTHONPATH"] = os.pathsep.join(
            part for part in (source, environment.get("PYTHONPATH")) if part
        )
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=root, env=environment,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_unknown_task_or_missing_candidate_is_quiet(self):
        self.assertIsNone(advisor.evaluate({"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Hello"}))
        with mock.patch.object(advisor, "candidates", return_value=[]), mock.patch.object(advisor, "DecisionsClient") as client:
            self.assertIsNone(advisor.evaluate({"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": "Investigate a Python runtime error"}))
            client.assert_not_called()


    def test_security_requirements_candidate_is_gated_by_local_relevance(self):
        security_skill = {
            "id": "security-requirement-extraction", "kind": "skill",
            "capability": "Extract security requirements and controls",
            "use_when": "security requirements, authentication, and signed webhook reviews",
            "avoid_when": "ordinary correctness review", "availability": "available",
        }
        ordinary = "Review this Python parser for correctness and regressions."
        relevant_prompts = (
            "Plan for security requirements for authentication and signed webhook verification.",
            "Review the Python authentication flow for security requirements.",
            "Review whether signed webhook verification meets its security requirements.",
        )
        sentinel = "private-client-token-6db301"
        explicit = (
            "Review the Python authentication flow for security requirements and signed webhook "
            f"verification. Keep this private value out of advice metadata: {sentinel}"
        )

        def candidate_ids_for(prompt):
            with mock.patch.object(advisor, "candidates", return_value=[*ITEMS, security_skill]), \
                    mock.patch.object(advisor, "_read_cache", return_value=None), \
                    mock.patch.object(advisor, "_write_cache"), \
                    mock.patch.object(advisor, "_metric"), \
                    mock.patch.object(advisor, "DecisionsClient") as client:
                client.return_value.decide.return_value = answers(answer="serena")
                advisor.evaluate({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
                request = json.dumps(client.return_value.decide.call_args.args)
            self.assertNotIn(sentinel, request)
            payload = json.loads(request)[0]["candidates"]
            return {item["id"] for item in payload}

        self.assertNotIn("security-requirement-extraction", candidate_ids_for(ordinary))
        for prompt in (*relevant_prompts, explicit):
            with self.subTest(prompt=prompt):
                self.assertIn("security-requirement-extraction", candidate_ids_for(prompt))


if __name__ == "__main__":
    unittest.main()
