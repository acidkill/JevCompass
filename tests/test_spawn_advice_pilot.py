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

    @staticmethod
    def _session_meta(session_id, **extra):
        return {"type": "session_meta", "payload": {"id": session_id, **extra}}

    @staticmethod
    def _advisor_context(advice_id=None, include_skill=True):
        lines = []
        if advice_id:
            lines.append(f"JevCompass advice ID: {advice_id}")
        lines.append("Optional tools and skills for this task; validate against the task and actual availability:")
        if include_skill:
            lines.append("- skill `jevcompass-focused-tests`: Run the focused Python tests")
        lines.append("Inspect task scope first. Follow required project instructions and tests.")
        return "\n".join(lines)

    @staticmethod
    def _message_event(text, role="developer"):
        return {
            "type": "response_item",
            "payload": {
                "type": "message", "role": role,
                "content": [{"type": "input_text", "text": text}],
            },
        }

    @classmethod
    def _child_events(cls, advice_id="deadbeef", include_skill=True, advice_first=True, tool_args=None, advice_role="developer", prior_context=False):
        advice_event = cls._message_event(cls._advisor_context(advice_id, include_skill), role=advice_role)
        first_context = cls._message_event("Base developer context without an advice ID")
        tool = {
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec_command", "input": tool_args or "cat ~/.codex/skills/jevcompass-focused-tests/SKILL.md"},
        }
        fixture = {
            "type": "response_item",
            "payload": {"type": "custom_tool_call", "name": "exec_command", "input": "cat WEBHOOK_BRIEF.md"},
        }
        first = [advice_event, tool] if advice_first else [tool, advice_event]
        if prior_context:
            first.insert(0, first_context)
        return [json.dumps(row) for row in [*first, fixture]]

    def _write_pair(self, root, *, child_events=None, child_metadata=None, child_count=1):
        sessions = root / "sessions"
        sessions.mkdir(parents=True)
        root_file = sessions / "root.jsonl"
        root_file.write_text(json.dumps(self._session_meta("root-session")) + "\n")
        paths = [root_file]
        for index in range(child_count):
            child_file = sessions / f"child-{index}.jsonl"
            metadata = child_metadata if child_metadata is not None else {
                "forked_from_id": "root-session",
                "agent_nickname": "plan_python_webhook_tests",
                "agent_path": "/root/plan_python_webhook_tests",
            }
            rows = [self._session_meta(f"child-session-{index}", **metadata)]
            rows.extend(map(json.loads, child_events or self._child_events()))
            child_file.write_text("\n".join(map(json.dumps, rows)) + "\n")
            paths.append(child_file)
        return paths

    def test_persisted_child_needs_linked_identity_initial_advice_and_ordered_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_pair(Path(temporary))
            result = pilot.parse_persisted_child_sessions(
                paths, treatment=True, spawn_trace_ids=["deadbeef"],
            )
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["advice_report"]["advice_id"], "deadbeef")
        self.assertEqual(result["advice_report"]["selected_ids"], [pilot.SKILL_NAME])
        self.assertTrue(result["skill_read_observed"])
        self.assertTrue(result["first_fixture_read_observed"])

    def test_child_without_explicit_identity_and_parent_link_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_pair(Path(temporary), child_metadata={
                "agent_nickname": "plan_python_webhook_tests", "agent_path": "/root/plan_python_webhook_tests",
            })
            result = pilot.parse_persisted_child_sessions(paths, treatment=True)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["reason"], "child_session_identity_ambiguous")

    def test_advice_after_first_tool_is_not_initial_context_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_pair(Path(temporary), child_events=self._child_events(advice_first=False))
            result = pilot.parse_persisted_child_sessions(
                paths, treatment=True, spawn_trace_ids=["deadbeef"],
            )
        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["initial_context_before_first_tool"])

    def test_later_developer_context_before_first_tool_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_pair(Path(temporary), child_events=self._child_events(prior_context=True))
            result = pilot.parse_persisted_child_sessions(
                paths, treatment=True, spawn_trace_ids=["deadbeef"],
            )
        self.assertEqual(result["status"], "confirmed")

    def test_copied_advice_report_outside_developer_context_is_rejected(self):
        copied = "JevCompass advice ID: deadbeef; selected catalog IDs: jevcompass-focused-tests"
        events = [
            json.dumps(self._message_event("Base developer context without advice")),
            json.dumps(self._message_event(copied, role="assistant")),
            *self._child_events(advice_id=None)[1:],
        ]
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_pair(Path(temporary), child_events=events)
            result = pilot.parse_persisted_child_sessions(
                paths, treatment=True, spawn_trace_ids=["deadbeef"],
            )
        self.assertEqual(result["status"], "inconclusive")
        self.assertIsNone(result["advice_report"])

    def test_treatment_requires_specific_skill_in_advice_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_pair(Path(temporary), child_events=self._child_events(include_skill=False))
            result = pilot.parse_persisted_child_sessions(
                paths, treatment=True, spawn_trace_ids=["deadbeef"],
            )
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["advice_report"]["selected_ids"], [])

    def test_skill_after_bounded_advisor_candidate_list_is_ignored(self):
        bullets = [f"- tool `candidate-{index}`: synthetic candidate" for index in range(6)]
        text = "JevCompass advice ID: deadbeef\n" + "\n".join([
            *bullets,
            "- skill `jevcompass-focused-tests`: synthetic skill",
        ])
        self.assertFalse(pilot._selected_known_skill(text))

    def test_baseline_confirms_no_advice_with_ordered_child_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_pair(Path(temporary), child_events=self._child_events(advice_id="faceb00c"))
            result = pilot.parse_persisted_child_sessions(paths, treatment=False)
        self.assertEqual(result["status"], "confirmed")
        self.assertIsNone(result["advice_report"])

    def test_spawn_trace_selects_matching_skill_context_after_default_advice(self):
        contexts = [
            json.dumps(self._message_event(self._advisor_context("11111111", include_skill=False))),
            json.dumps(self._message_event(self._advisor_context("22222222", include_skill=True))),
        ]
        events = [*contexts, *self._child_events(advice_id=None)[1:]]
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_pair(Path(temporary), child_events=events)
            result = pilot.parse_persisted_child_sessions(
                paths, treatment=True, spawn_trace_ids=["22222222"],
            )
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["advice_report"]["advice_id"], "22222222")
        self.assertEqual(result["advice_report"]["selected_ids"], [pilot.SKILL_NAME])
        self.assertEqual(result["advice_context_count"], 2)

    def test_multiple_pretool_traces_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_pair(Path(temporary), child_events=self._child_events())
            result = pilot.parse_persisted_child_sessions(
                paths, treatment=True, spawn_trace_ids=["deadbeef", "cafebabe"],
            )
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["reason"], "spawn_metric_ambiguous_or_missing")
        self.assertIsNone(result["advice_report"])

    def test_unrelated_tool_paths_do_not_count_as_reviewed_reads(self):
        args = "cat /tmp/jevcompass-focused-tests/SKILL.md.txt /tmp/OTHER_WEBHOOK_BRIEF.md"
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_pair(Path(temporary), child_events=self._child_events(tool_args=args))
            result = pilot.parse_persisted_child_sessions(
                paths, treatment=True, spawn_trace_ids=["deadbeef"],
            )
        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["skill_read_observed"])
        self.assertFalse(result["first_fixture_read_observed"])

    def test_multiple_linked_children_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_pair(Path(temporary), child_count=2)
            result = pilot.parse_persisted_child_sessions(paths, treatment=True)
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["reason"], "child_session_identity_ambiguous")

    def test_live_arm_uses_persisted_sessions_without_ephemeral_flag(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            codex_home = home / ".codex"
            codex_home.mkdir(parents=True)
            auth = codex_home / "auth.json"
            auth.write_text("synthetic")
            auth.chmod(0o600)
            fixture = root / "fixture"
            fixture.mkdir()
            profile = {
                "codex_home": codex_home,
                "env": {"CODEX_HOME": str(codex_home)},
                "fixture": fixture,
            }
            self._write_pair(codex_home, child_events=self._child_events())
            metrics = home / ".local/state/jevcompass/advisor.jsonl"
            metrics.parent.mkdir(parents=True)
            metrics.write_text(json.dumps({
                "event": "PreToolUse", "status": "jev", "trace": "deadbeef",
            }) + "\n")
            process = type("Process", (), {"returncode": 0, "stdout": None})()
            with mock.patch.object(pilot.subprocess, "Popen", return_value=process) as popen, \
                    mock.patch.object(pilot, "_collect", return_value=([], None)):
                result = pilot._run_arm(
                    codex="/synthetic/codex", model="synthetic-model", arm="treatment",
                    profile=profile, timeout=pilot.DEFAULT_TIMEOUT,
                )
            command = popen.call_args.args[0]
        self.assertNotIn("--ephemeral", command)
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["spawn_advice_correlated_to_child"])
        self.assertEqual(result["delivery_status"], "confirmed")
        self.assertTrue(result["child_session_observed"])

    def test_delivery_can_be_confirmed_when_fixture_read_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            codex_home = home / ".codex"
            codex_home.mkdir(parents=True)
            auth = codex_home / "auth.json"
            auth.write_text("synthetic")
            auth.chmod(0o600)
            fixture = root / "fixture"
            fixture.mkdir()
            events = self._child_events()[:-1]
            self._write_pair(codex_home, child_events=events)
            metrics = home / ".local/state/jevcompass/advisor.jsonl"
            metrics.parent.mkdir(parents=True)
            metrics.write_text(json.dumps({
                "event": "PreToolUse", "status": "jev", "trace": "deadbeef",
            }) + "\n")
            profile = {
                "codex_home": codex_home,
                "env": {"CODEX_HOME": str(codex_home)},
                "fixture": fixture,
            }
            process = type("Process", (), {"returncode": 0, "stdout": None})()
            with mock.patch.object(pilot.subprocess, "Popen", return_value=process), \
                    mock.patch.object(pilot, "_collect", return_value=([], None)):
                result = pilot._run_arm(
                    codex="/synthetic/codex", model="synthetic-model", arm="treatment",
                    profile=profile, timeout=pilot.DEFAULT_TIMEOUT,
                )
        self.assertEqual(result["delivery_status"], "confirmed")
        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["first_fixture_read_observed"])

    def test_summary_does_not_retain_raw_child_content_or_paths(self):
        secret = "synthetic-private-prompt-value-90817"
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._write_pair(Path(temporary), child_events=self._child_events())
            child_path = paths[1]
            raw = child_path.read_text()
            child_path.write_text(raw.replace(
                "~/.codex/skills/jevcompass-focused-tests/SKILL.md", secret,
            ))
            summary = json.dumps(pilot.parse_persisted_child_sessions(
                paths, treatment=True, spawn_trace_ids=["deadbeef"],
            ))
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
