"""Offline contract tests for the opt-in synthetic spawn-advice harness."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import pilot_spawn_advice_pair as pilot  # noqa: E402
from jevcompass.advisor import _spawn_intent  # noqa: E402


class SpawnAdvicePilotTests(unittest.TestCase):
    def test_pair_profiles_are_contained_and_identical_except_spawn_hook(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = pilot.prepare_pair(root)
            baseline = plan["profiles"]["baseline"]
            treatment = plan["profiles"]["treatment"]

            self.assertEqual(baseline["env"]["HOME"], str(root / "baseline/home"))
            self.assertEqual(baseline["env"]["CODEX_HOME"], str(root / "baseline/home/.codex"))
            self.assertNotEqual(baseline["env"]["XDG_CACHE_HOME"], treatment["env"]["XDG_CACHE_HOME"])
            for arm, profile in plan["profiles"].items():
                self.assertTrue(Path(profile["env"]["XDG_CONFIG_HOME"]).is_relative_to(root / arm))
                self.assertTrue(Path(profile["env"]["XDG_CACHE_HOME"]).is_relative_to(root / arm))
                self.assertTrue(Path(profile["env"]["XDG_STATE_HOME"]).is_relative_to(root / arm))
            self.assertTrue(plan["fixture_copies_identical"])
            self.assertTrue(plan["skill_copies_identical"])
            self.assertTrue(plan["default_configuration_equal"])
            self.assertEqual(plan["fixture_sha256"], pilot.fixture_digest())

    def test_hook_pair_allows_only_exact_spawn_matcher_in_treatment(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan = pilot.prepare_pair(Path(temporary))
            baseline_path = plan["profiles"]["baseline"]["codex_home"] / "hooks.json"
            treatment_path = plan["profiles"]["treatment"]["codex_home"] / "hooks.json"
            baseline = json.loads(baseline_path.read_text())
            treatment = json.loads(treatment_path.read_text())
            group = next(
                item for item in treatment["hooks"]["PreToolUse"]
                if item["matcher"] == pilot.SPAWN_MATCHER
            )
            self.assertEqual(group["matcher"], "^(Agent|spawn_agent|collaborationspawn_agent)$")
            self.assertEqual(len(group["hooks"]), 1)
            self.assertTrue(pilot.verify_hook_pair(baseline, treatment))

            broad = json.loads(json.dumps(treatment))
            broad["hooks"]["PreToolUse"][-1]["matcher"] = "*"
            self.assertFalse(pilot.verify_hook_pair(baseline, broad))

    def test_task_name_is_specific_python_testing_intent(self):
        event = {
            "tool_name": "Agent",
            "tool_input": {"task_name": "plan_python_webhook_tests", "agent_type": "explorer"},
        }
        self.assertEqual(_spawn_intent(event), ("testing", "python", "explorer"))

    def test_auth_copy_is_private_and_refuses_missing_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source-auth.json"
            source.write_text('{"token":"synthetic"}')
            destination = root / "profile/.codex/auth.json"
            self.assertTrue(pilot._copy_auth(source, destination))
            self.assertEqual(os.stat(destination).st_mode & 0o777, 0o600)
            self.assertFalse(pilot._copy_auth(root / "missing", root / "other/auth.json"))

    def test_child_delivery_needs_real_id_advice_and_ordered_reads(self):
        lines = [
            json.dumps({"type": "item.started", "item": {"type": "collaboration_tool_call", "name": "spawn_agent"}}),
            json.dumps({"type": "item.completed", "agent_id": "child-123", "agent_type": "explorer", "item": {
                "type": "agent_message",
                "text": "JevCompass advice ID: deadbeef; selected catalog IDs: jevcompass-focused-tests",
            }}),
            json.dumps({"type": "item.started", "agent_id": "child-123", "agent_type": "explorer", "item": {
                "type": "command_execution", "command": "cat .codex/skills/jevcompass-focused-tests/SKILL.md",
            }}),
            json.dumps({"type": "item.started", "agent_id": "child-123", "agent_type": "explorer", "item": {
                "type": "command_execution", "command": "cat WEBHOOK_BRIEF.md",
            }}),
        ]
        result = pilot.parse_child_transcript(lines)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["child_id"], "child-123")
        self.assertEqual(result["advice_report"]["advice_id"], "deadbeef")
        self.assertTrue(result["skill_read_observed"])
        self.assertTrue(result["first_fixture_read_observed"])

    def test_child_without_explicit_id_is_inconclusive(self):
        lines = [json.dumps({"type": "item.completed", "item": {
            "agent_type": "explorer", "type": "agent_message", "text": "JevCompass advice ID: deadbeef; selected catalog IDs: none",
        }})]
        result = pilot.parse_child_transcript(lines)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["reason"], "child_identity_unobservable")
        self.assertNotIn("child_id", result)

    def test_summary_does_not_retain_raw_child_content_or_paths(self):
        secret = "synthetic-private-prompt-value-90817"
        lines = [
            json.dumps({"type": "item.started", "item": {"type": "tool_call", "name": "Agent", "message": secret}}),
            json.dumps({"type": "item.completed", "agent_id": "child-123", "agent_type": "explorer", "item": {
                "type": "agent_message",
                "text": "JevCompass advice ID: deadbeef; selected catalog IDs: none. " + secret,
            }}),
        ]
        summary = json.dumps(pilot.parse_child_transcript(lines))
        self.assertNotIn(secret, summary)
        self.assertNotIn(str(ROOT), summary)

    def test_timeout_is_bounded_and_classified(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdout=subprocess.PIPE,
        )
        lines, failure = pilot._collect(process, 1)
        self.assertEqual(lines, [])
        self.assertEqual(failure, "timeout")
        self.assertIsNotNone(process.poll())
        with self.assertRaises(ValueError):
            pilot.run_pair(live=False, model=None, timeout=pilot.MAX_TIMEOUT + 1)

    def test_dry_run_never_starts_a_process(self):
        with mock.patch.object(pilot.subprocess, "Popen", side_effect=AssertionError("process started")):
            result = pilot.run_pair(live=False, model=None, timeout=pilot.DEFAULT_TIMEOUT)
        self.assertEqual(result["status"], "dry_run_verified")
        self.assertFalse(result["network_or_model_called"])
        self.assertTrue(result["fixture_copies_identical"])
        self.assertTrue(result["skill_copies_identical"])
        self.assertTrue(result["default_configuration_equal"])


if __name__ == "__main__":
    unittest.main()
