from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from jevcompass import advisor, cli
from jevcompass.installer import SUBAGENT_MATCHER


class DoctorTests(unittest.TestCase):
    def test_doctor_separates_registered_hooks_from_safe_observed_metric(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / "codex"
            codex_home.mkdir()
            (codex_home / "hooks.json").write_text(json.dumps({"hooks": {
                "UserPromptSubmit": [{"hooks": [{"command": advisor.hook_command()}]}],
                "SubagentStart": [{"matcher": SUBAGENT_MATCHER, "hooks": [{"command": advisor.hook_command()}]}],
            }}))
            metric = root / "advisor.jsonl"
            metric.write_text(json.dumps({
                "event": "UserPromptSubmit",
                "category": "coding",
                "status": "low-signal-skip",
                "duration_ms": 12,
                "trace": "private-trace-token",
                "prompt": "private prompt text",
                "path": "/private/worktree",
                "secret": "private-secret",
            }) + "\n")
            os.utime(metric, (time.time() - 37, time.time() - 37))
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}), \
                    mock.patch.object(advisor, "LOG_PATH", metric), \
                    mock.patch.object(cli, "model_status", return_value="available"), \
                    mock.patch.object(cli, "catalog_snapshot", return_value=([{"id": "exec_command"}], {"curated_entries": 1, "available_tools": 1, "available_skills": 0,
                        "configured_mcp_servers": 0, "discovered_skills": 0, "unavailable_entries": 0})):
                result = cli.doctor()
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = cli.main(["doctor", "--json"])
                text_output = io.StringIO()
                with contextlib.redirect_stdout(text_output):
                    cli.main(["doctor"])
        observation = result["hook_observation"]
        self.assertEqual(result["hooks_json"]["registered_advisory_hooks"], ["UserPromptSubmit", "SubagentStart"])
        self.assertTrue(observation["observed"])
        self.assertEqual(observation["event"], "UserPromptSubmit")
        self.assertEqual(observation["status"], "low-signal-skip")
        self.assertGreaterEqual(observation["log_modified_age_seconds"], 37)
        self.assertLessEqual(observation["log_modified_age_seconds"], 40)
        self.assertEqual(exit_code, 0)
        self.assertIn("Hooks registered: UserPromptSubmit, SubagentStart", text_output.getvalue())
        self.assertIn("Hook invocation metric: UserPromptSubmit;", text_output.getvalue())
        self.assertIn("log modified", text_output.getvalue())
        self.assertIn("status low-signal-skip", text_output.getvalue())
        serialized = json.dumps(result) + output.getvalue() + text_output.getvalue()
        for private_value in ("private-trace-token", "private prompt text", "/private/worktree", "private-secret"):
            self.assertNotIn(private_value, serialized)

    def test_observation_reports_each_hook_without_leaking_metric_content(self):
        with tempfile.TemporaryDirectory() as directory:
            metric = Path(directory) / "advisor.jsonl"
            metric.write_text("\n".join((
                json.dumps({"event": "SubagentStart", "status": "low-signal-skip",
                            "trace": "sensitive-trace", "prompt": "private task"}),
                "invalid-json",
                json.dumps({"event": "UserPromptSubmit", "status": "jev",
                            "secret": "sensitive-secret"}),
            )) + "\n")
            with mock.patch.object(advisor, "LOG_PATH", metric):
                observation = cli._hook_observation()
        self.assertEqual(observation["event"], "UserPromptSubmit")
        self.assertEqual(observation["recent_by_event"], {
            "UserPromptSubmit": "jev", "SubagentStart": "low-signal-skip"})
        serialized = json.dumps(observation)
        for value in ("sensitive-trace", "private task", "sensitive-secret"):
            self.assertNotIn(value, serialized)

    def test_doctor_reports_registered_hooks_without_claiming_observed_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / "codex"
            codex_home.mkdir()
            (codex_home / "hooks.json").write_text(json.dumps({"hooks": {
                "UserPromptSubmit": [{"hooks": [{"command": advisor.hook_command()}]}],
                "SubagentStart": [{"matcher": SUBAGENT_MATCHER, "hooks": [{"command": advisor.hook_command()}]}],
            }}))
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}), \
                    mock.patch.object(advisor, "LOG_PATH", root / "missing.jsonl"), \
                    mock.patch.object(cli, "model_status", return_value="available"), \
                    mock.patch.object(cli, "catalog_snapshot", return_value=([{"id": "exec_command"}], {"curated_entries": 1, "available_tools": 1, "available_skills": 0,
                        "configured_mcp_servers": 0, "discovered_skills": 0, "unavailable_entries": 0})):
                result = cli.doctor()
        self.assertEqual(result["hooks_json"]["registered_advisory_hooks"], ["UserPromptSubmit", "SubagentStart"])
        self.assertFalse(result["hook_observation"]["observed"])
        self.assertIsNone(result["hook_observation"]["event"])
        self.assertEqual(result["hook_observation"]["status"], "unavailable")

    def test_doctor_uses_codex_home_and_reports_only_safe_status(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory) / "codex-profile"
            codex_home.mkdir()
            (codex_home / "hooks.json").write_text(json.dumps({"hooks": {
                "UserPromptSubmit": [{"hooks": [{"command": advisor.hook_command()}]}],
                "SubagentStart": [{"matcher": SUBAGENT_MATCHER, "hooks": [{"command": advisor.hook_command()}]}],
                "PreToolUse": [],
            }}))
            secret_key = "openrouter-secret-test-value"
            with mock.patch.dict(os.environ, {
                "CODEX_HOME": str(codex_home),
                "OPENROUTER_API_KEY": secret_key,
                "JEVCOMPASS_MODEL": "custom-model-hidden-96a",
            }), mock.patch.object(cli, "model_status", return_value="available"), \
                    mock.patch.object(cli, "catalog_snapshot", return_value=(
                        [{"id": "python-testing-patterns"}],
                        {
                            "curated_entries": 20,
                            "available_tools": 3,
                            "available_skills": 1,
                            "configured_mcp_servers": 2,
                            "discovered_skills": 8,
                            "unavailable_entries": 16,
                        },
                    )):
                result = cli.doctor()
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = cli.main(["doctor"])
            serialized = json.dumps(result) + output.getvalue()
            self.assertTrue(result["ok"])
            self.assertEqual(result["codex_home"]["source"], "environment")
            self.assertTrue(result["hooks_json"]["ok"])
            self.assertEqual(exit_code, 0)
            self.assertIn("CODEX_HOME override", output.getvalue())
            self.assertIn("2 MCP servers configured", output.getvalue())
            self.assertIn("do not prove tools are callable", output.getvalue())
            self.assertNotIn(str(codex_home), serialized)
            self.assertNotIn(secret_key, serialized)
            self.assertNotIn("custom-model-hidden-96a", serialized)

    def test_doctor_rejects_subagent_hook_with_wrong_matcher(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            (codex_home / "hooks.json").write_text(json.dumps({"hooks": {
                "UserPromptSubmit": [{"hooks": [{"command": advisor.hook_command()}]}],
                "SubagentStart": [{"matcher": "^default$", "hooks": [{"command": advisor.hook_command()}]}],
            }}))
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}), \
                    mock.patch.object(cli, "model_status", return_value="available"), \
                    mock.patch.object(cli, "catalog_snapshot", return_value=([{"id": "exec_command"}], {"curated_entries": 1, "available_tools": 1, "available_skills": 0,
                        "configured_mcp_servers": 0, "discovered_skills": 0, "unavailable_entries": 0})):
                result = cli.doctor()
        self.assertFalse(result["hooks_json"]["ok"])
        self.assertEqual(result["hooks_json"]["registered_advisory_hooks"], ["UserPromptSubmit"])

    def test_doctor_reports_hooks_disabled_in_base_config(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            (codex_home / "hooks.json").write_text(json.dumps({"hooks": {
                "UserPromptSubmit": [{"hooks": [{"command": advisor.hook_command()}]}],
                "SubagentStart": [{"matcher": SUBAGENT_MATCHER, "hooks": [{"command": advisor.hook_command()}]}],
            }}))
            for setting in ("hooks", "codex_hooks"):
                with self.subTest(setting=setting):
                    (codex_home / "config.toml").write_text(f"[features]\n{setting} = false\n")
                    with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}), \
                            mock.patch.object(cli, "model_status", return_value="available"), \
                            mock.patch.object(cli, "catalog_snapshot", return_value=([{"id": "exec_command"}], {"curated_entries": 1, "available_tools": 1, "available_skills": 0,
                        "configured_mcp_servers": 0, "discovered_skills": 0, "unavailable_entries": 0})):
                        result = cli.doctor()
                    self.assertTrue(result["hooks_json"]["ok"])
                    self.assertEqual(result["hooks_feature"], {"ok": False, "base_config": "disabled"})
                    self.assertFalse(result["ok"])

    def test_doctor_without_openrouter_key_explains_local_fallback(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/jevcompass-doctor-profile"}, clear=True), \
                mock.patch.object(cli, "model_status", return_value="unavailable"), \
                mock.patch.object(cli, "catalog_snapshot", return_value=(
                    [{"id": "exec_command"}],
                    {
                        "curated_entries": 20,
                        "available_tools": 3,
                        "available_skills": 1,
                        "configured_mcp_servers": 0,
                        "discovered_skills": 1,
                        "unavailable_entries": 16,
                    },
                )):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = cli.main(["doctor"])
            result = cli.doctor()
        self.assertEqual(exit_code, 1)  # This fixture omits hook registration.
        self.assertEqual(result["jev_model"]["status"], "unavailable")
        self.assertFalse(result["jev_model"]["available"])
        self.assertTrue(result["jev_model"]["ok"])
        self.assertIn("not configured", output.getvalue())
        self.assertIn("local single-candidate advice can still work", output.getvalue())
        self.assertIn("metadata check unavailable; remote advice is unverified", output.getvalue())
        self.assertIn("path hidden", output.getvalue())

    def test_doctor_passes_local_setup_without_optional_openrouter_key(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            (codex_home / "hooks.json").write_text(json.dumps({"hooks": {
                "UserPromptSubmit": [{"hooks": [{"command": advisor.hook_command()}]}],
                "SubagentStart": [{"matcher": SUBAGENT_MATCHER, "hooks": [{"command": advisor.hook_command()}]}],
            }}))
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=True), \
                    mock.patch.object(cli, "credential_status", return_value={
                        "configured": False, "source": "none", "secure_store_available": False,
                    }), \
                    mock.patch.object(cli, "model_status", return_value="unavailable"), \
                    mock.patch.object(cli, "catalog_snapshot", return_value=([{"id": "exec_command"}], {
                        "curated_entries": 1, "available_tools": 1, "available_skills": 0,
                        "configured_mcp_servers": 0, "discovered_skills": 0, "unavailable_entries": 0,
                    })):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = cli.main(["doctor", "--json"])
        result = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertFalse(result["openrouter_key"]["ok"])
        self.assertEqual(result["jev_model"]["status"], "unavailable")
        self.assertTrue(result["jev_model"]["ok"])
        self.assertTrue(result["ok"])

    def test_doctor_fails_when_metadata_confirms_model_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            (codex_home / "hooks.json").write_text(json.dumps({"hooks": {
                "UserPromptSubmit": [{"hooks": [{"command": advisor.hook_command()}]}],
                "SubagentStart": [{"matcher": SUBAGENT_MATCHER, "hooks": [{"command": advisor.hook_command()}]}],
            }}))
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=True), \
                    mock.patch.object(cli, "credential_status", return_value={
                        "configured": False, "source": "none", "secure_store_available": False,
                    }), \
                    mock.patch.object(cli, "model_status", return_value="missing"), \
                    mock.patch.object(cli, "catalog_snapshot", return_value=([{"id": "exec_command"}], {
                        "curated_entries": 1, "available_tools": 1, "available_skills": 0,
                        "configured_mcp_servers": 0, "discovered_skills": 0, "unavailable_entries": 0,
                    })):
                result = cli.doctor()
        self.assertEqual(result["jev_model"]["status"], "missing")
        self.assertFalse(result["jev_model"]["ok"])
        self.assertFalse(result["ok"])

    def test_doctor_uses_redacted_system_keyring_status(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(
                os.environ,
                {"CODEX_HOME": "/tmp/jevcompass-doctor-profile"},
                clear=True,
            ))
            stack.enter_context(mock.patch.object(cli, "credential_status", return_value={
                "configured": True,
                "source": "system-keyring",
                "secure_store_available": True,
            }))
            stack.enter_context(mock.patch.object(cli, "model_status", return_value="available"))
            stack.enter_context(mock.patch.object(cli, "catalog_snapshot", return_value=(
                [{"id": "exec_command"}],
                {
                    "curated_entries": 20,
                    "available_tools": 3,
                    "available_skills": 1,
                    "configured_mcp_servers": 0,
                    "discovered_skills": 1,
                    "unavailable_entries": 16,
                },
            )))
            result = cli.doctor()
        self.assertTrue(result["openrouter_key"]["ok"])
        self.assertEqual(result["openrouter_key"]["source"], "system-keyring")
        self.assertTrue(result["openrouter_key"]["secure_store_available"])

    def test_selection_capacity_distinguishes_singleton_from_real_choice(self):
        entries = [
            {"id": "exec_command", "kind": "tool", "availability": "available",
             "task_kinds": ["source-review", "codebase", "code"],
             "domains": ["general", "software"]},
            {"id": "configured-mcp", "kind": "tool", "availability": "configured",
             "task_kinds": ["source-review"], "domains": ["general"]},
            {"id": "review-skill", "kind": "skill", "availability": "available",
             "task_kinds": ["source-review"], "domains": ["general"]},
        ]
        examples = cli._selection_capacity(entries)["examples"]
        self.assertEqual(examples["source_review_general"]["mode"], "local_candidates")
        self.assertEqual(examples["source_review_general"]["available_tools"], 1)
        self.assertEqual(examples["codebase_software"]["mode"], "low_signal_skip")
        self.assertEqual(examples["coding_python"]["mode"], "low_signal_skip")
        specific_singleton = [{"id": "pytest", "kind": "tool", "availability": "available",
                               "task_kinds": ["code"], "domains": ["python"]}]
        self.assertEqual(
            cli._selection_capacity(specific_singleton)["examples"]["coding_python"]["mode"],
            "local_candidates",
        )
        entries.append({"id": "rg", "kind": "tool", "availability": "available",
                        "task_kinds": ["codebase"], "domains": ["software"]})
        examples = cli._selection_capacity(entries)["examples"]
        self.assertEqual(examples["codebase_software"]["mode"], "decision_candidates")
        self.assertEqual(examples["source_review_general"]["available_tools"], 1)
        self.assertEqual(cli._selection_capacity([])["examples"]["coding_python"]["mode"], "silent")


if __name__ == "__main__":
    unittest.main()
