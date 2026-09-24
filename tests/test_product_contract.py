from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jevcompass import advisor, cli, installer
from jevcompass.catalog import candidates, load_catalog


class ProductContractTests(unittest.TestCase):
    def test_new_repository_and_package_plan_is_project_setup_not_documents(self):
        prompt = "Plan creating a new private GitHub repository and product package with README"
        self.assertEqual(advisor.classify_task(prompt), ("project-setup", "software"))
        project_candidates = candidates("project", "any", "software", limit=20)
        ids = {item["id"] for item in project_candidates}
        self.assertIn("python-packaging", {entry["id"] for entry in load_catalog()})
        self.assertNotIn("documents", ids)

    def test_polish_repository_package_prompt_uses_project_setup(self):
        prompt = "Zaplanuj stworzenie nowego prywatnego repozytorium GitHub i pakietu produktowego z README"
        self.assertEqual(advisor.classify_task(prompt), ("project-setup", "software"))


    def test_jev_shortlist_is_small_and_balanced(self):
        pool = [
            {"id": f"tool-{number}", "kind": "tool"} for number in range(5)
        ] + [
            {"id": f"skill-{number}", "kind": "skill"} for number in range(4)
        ]
        shortlist = advisor._balanced_shortlist(pool, per_kind=3)
        self.assertEqual(sum(item["kind"] == "tool" for item in shortlist), 3)
        self.assertEqual(sum(item["kind"] == "skill" for item in shortlist), 3)
        self.assertLessEqual(len(shortlist), 6)

    def test_hook_request_contains_only_allowlisted_metadata(self):
        secret = "private-acme-code-and-token-123"
        prompt = f"Create a new Python package for {secret}"
        with mock.patch.object(advisor, "candidates", return_value=[
            {"id": "exec_command", "kind": "tool", "capability": "Bounded local operations",
             "use_when": "inspect local project", "avoid_when": "unclear mutations", "availability": "available"},
            {"id": "git", "kind": "tool", "capability": "Inspect repository state",
             "use_when": "review repository metadata", "avoid_when": "destructive changes", "availability": "available"},
            {"id": "python-packaging", "kind": "skill", "capability": "Package Python projects",
             "use_when": "build a Python distribution", "avoid_when": "not Python packaging", "availability": "available"},
            {"id": "create-plan", "kind": "skill", "capability": "Create an implementation plan",
             "use_when": "sequence project work", "avoid_when": "trivial operation", "availability": "available"},
        ]), mock.patch.object(advisor, "_read_cache", return_value=None), \
             mock.patch.object(advisor, "_judge", return_value=["exec_command", "python-packaging"]) as judge, \
             mock.patch.object(advisor, "_write_cache"), mock.patch.object(advisor, "_metric"):
            output = advisor.evaluate({"hook_event_name": "UserPromptSubmit", "permission_mode": "plan", "prompt": prompt})
        request = json.dumps(judge.call_args.args)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn(secret, request + context)
        self.assertIn("python-packaging", context)

    def test_configured_mcp_is_marked_as_unverified_in_advice(self):
        item = {"id": "context7", "kind": "tool", "capability": "Current documentation",
                "availability": "configured"}
        result = advisor._context("SubagentStart", ["context7"], [item])
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("confirm it is connected in this session", context)

    def test_catalog_has_no_personal_ids_and_descriptions_are_curated(self):
        entries = load_catalog()
        self.assertFalse(any(entry["id"].startswith("tonis-") for entry in entries))
        for item in entries:
            self.assertTrue(item["use_when"])
            self.assertTrue(item["avoid_when"])

    def test_recommend_requires_allowlisted_category_domain_and_role(self):
        with mock.patch.object(advisor, "select_advice", return_value=None) as select:
            self.assertEqual(cli.main(["recommend", "--category", "project-setup", "--domain", "software"]), 0)
            select.assert_called_once_with("UserPromptSubmit", "project-setup", "software", "primary")
        with self.assertRaises(SystemExit):
            cli.main(["recommend", "--category", "arbitrary-prompt", "--domain", "software"])

    def test_install_merge_removes_only_known_jev_gate_and_preserves_smem(self):
        data = {"hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "/home/user/.local/bin/codex-jev-risk-router"},
                    {"type": "command", "command": "smem audit", "timeout": 10},
                ]}
            ],
            "Stop": [{"hooks": [{"type": "command", "command": "smem stop"}]}],
        }}
        merged = installer.merge_hooks(data, "/usr/bin/python3 -m jevcompass hook")
        hooks = merged["hooks"]
        self.assertEqual([handler["command"] for handler in hooks["PreToolUse"][0]["hooks"]], ["smem audit"])
        self.assertEqual(hooks["Stop"], data["hooks"]["Stop"])
        self.assertEqual(len(hooks["UserPromptSubmit"]), 1)
        self.assertEqual(len(hooks["SubagentStart"]), 1)
        self.assertEqual(hooks["SubagentStart"][0]["matcher"], "^(explorer|worker|luna_worker)$")

    def test_clean_profile_install_is_idempotent_and_preserves_smem_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            hooks_path = home / ".codex/hooks.json"
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_text(json.dumps({"hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "command": "smem session"}]}],
                "PreToolUse": [{"matcher": "Bash", "hooks": [
                    {"type": "command", "command": "npx -y jev-use hook gate"},
                    {"type": "command", "command": "smem pretool"},
                ]}],
            }}))
            with mock.patch.object(installer.shutil, "which", return_value=None):
                installer.install(home=home)
                first = json.loads(hooks_path.read_text())
                backups_after_first = list(home.glob(".codex/hooks.json.bak-jevcompass-*"))
                installer.install(home=home)
            second = json.loads(hooks_path.read_text())
            self.assertEqual(first, second)
            self.assertEqual(len(backups_after_first), 1)
            self.assertEqual(len(list(home.glob(".codex/hooks.json.bak-jevcompass-*"))), 1)
            self.assertEqual(first["hooks"]["SessionStart"][0]["hooks"][0]["command"], "smem session")
            self.assertEqual(first["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "smem pretool")
            self.assertFalse(any("jev" in handler.get("command", "") for group in first["hooks"]["PreToolUse"] for handler in group["hooks"]))

    def test_dry_run_does_not_create_user_config(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with mock.patch.object(installer.shutil, "which", return_value=None):
                message = installer.install(home=home, dry_run=True)
            self.assertIn("no files changed", message)
            self.assertFalse((home / ".codex/hooks.json").exists())
            self.assertFalse((home / ".local/share/jevcompass").exists())


if __name__ == "__main__":
    unittest.main()
