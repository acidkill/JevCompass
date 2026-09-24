from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jevcompass import advisor, cli
from jevcompass.installer import SUBAGENT_MATCHER


class DoctorTests(unittest.TestCase):
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
            }), mock.patch.object(cli, "model_available", return_value=True), \
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
                    mock.patch.object(cli, "model_available", return_value=True), \
                    mock.patch.object(cli, "catalog_snapshot", return_value=([{"id": "exec_command"}], {})):
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
                            mock.patch.object(cli, "model_available", return_value=True), \
                            mock.patch.object(cli, "catalog_snapshot", return_value=([{"id": "exec_command"}], {})):
                        result = cli.doctor()
                    self.assertTrue(result["hooks_json"]["ok"])
                    self.assertEqual(result["hooks_feature"], {"ok": False, "base_config": "disabled"})
                    self.assertFalse(result["ok"])

    def test_doctor_without_openrouter_key_explains_local_fallback(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/jevcompass-doctor-profile"}, clear=True), \
                mock.patch.object(cli, "model_available", return_value=False), \
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
        self.assertEqual(exit_code, 1)
        self.assertIn("not configured", output.getvalue())
        self.assertIn("local single-candidate advice can still work", output.getvalue())
        self.assertIn("metadata unavailable; remote advice will be skipped", output.getvalue())
        self.assertIn("path hidden", output.getvalue())

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
            stack.enter_context(mock.patch.object(cli, "model_available", return_value=True))
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


if __name__ == "__main__":
    unittest.main()
