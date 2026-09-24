"""Offline tests for the bounded W02 paired runner."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_w02_pair.py"
SPEC = importlib.util.spec_from_file_location("pilot_w02_pair", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


class W02PairRunnerTests(unittest.TestCase):
    def test_mock_pair_copies_fixture_and_emits_only_summary_indicators(self):
        result = runner.run_pair(mode="mock", model=None, timeout=3)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["fixture_copies_identical"])
        self.assertEqual(result["arms"]["treatment"]["advice_metric"]["metric_correlated"], True)
        self.assertTrue(result["arms"]["treatment"]["advice_id_before_first_tool"])
        self.assertFalse(result["arms"]["baseline"]["advice_id_before_first_tool"])
        self.assertEqual(
            result["arms"]["treatment"]["deterministic_indicators"],
            {
                "canonical_price_indicator": True,
                "missing_source_count_indicator": True,
                "intended_path_indicator": True,
                "unsent_draft_indicator": True,
            },
        )
        rendered = json.dumps(result)
        self.assertNotIn("Fjordly Labs data migration", rendered)
        self.assertNotIn("canonical-pricing.md).", rendered)
        self.assertNotIn("Proposal_Fjordly_Labs_Data_Migration_2026-10-15.md", rendered)

    def test_event_parser_correlates_only_advice_before_first_tool(self):
        lines = runner._safe_mock_lines()
        started = runner.time.monotonic()
        parsed = runner.parse_event_stream(
            lines, start_monotonic=started,
            event_times=[started + 0.1, started + 0.4],
        )
        self.assertEqual(parsed["event_count"], 2)
        self.assertAlmostEqual(parsed["events"][0]["elapsed_ms"], 100.0, delta=0.05)
        self.assertAlmostEqual(parsed["events"][1]["elapsed_ms"], 400.0, delta=0.05)
        self.assertEqual(parsed["events"][0]["kind"], "assistant")
        self.assertEqual(parsed["events"][1]["kind"], "tool")
        self.assertTrue(parsed["advice_id_before_first_tool"])
        self.assertEqual(parsed["advice_id"], "abcdef12")

    def test_diagnostic_metric_is_not_the_advice_decision(self):
        parsed = {"advice_id": "abcdef12", "advice_id_before_first_tool": True}
        metrics = [
            {"trace": "abcdef12", "category": "bypassPermissions", "status": "collab-unavailable"},
            {"trace": "abcdef12", "category": "source-review", "status": "local"},
        ]
        correlated = runner.correlate_advice(parsed, metrics)
        self.assertEqual(correlated["metric"]["category"], "source-review")

    def test_live_jsonl_collector_splits_lines_and_records_order(self):
        payload = "import sys; print('{\"type\":\"thread.started\"}'); print('{\"type\":\"turn.completed\"}'); sys.stdout.flush()"
        started = time.monotonic()
        process = subprocess.Popen([sys.executable, "-c", payload], stdout=subprocess.PIPE)
        lines, observed, failure = runner._collect_codex_events(process, started=started, timeout=3)
        self.assertIsNone(failure)
        self.assertEqual(len(lines), 2)
        self.assertEqual([json.loads(line)["type"] for line in lines], ["thread.started", "turn.completed"])
        self.assertEqual(len(observed), 2)
        self.assertLessEqual(observed[0], observed[1])

    def test_wrong_order_does_not_count_advice_as_pre_tool(self):
        lines = runner._safe_mock_lines()
        parsed = runner.parse_event_stream(list(reversed(lines)), start_monotonic=runner.time.monotonic())
        self.assertFalse(parsed["advice_id_before_first_tool"])
        self.assertIsNone(parsed["advice_id"])

    def test_error_items_do_not_count_as_first_tool(self):
        lines = [
            json.dumps({"type": "item.completed", "item": {"type": "error", "message": "synthetic"}}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "JevCompass advice ID: abcdef12"}}),
            json.dumps({"type": "item.started", "item": {"type": "command_execution"}}),
        ]
        parsed = runner.parse_event_stream(lines, start_monotonic=runner.time.monotonic())
        self.assertEqual(parsed["first_tool"]["order"], 3)
        self.assertTrue(parsed["advice_id_before_first_tool"])

    def test_auth_copy_is_private_and_does_not_expose_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source" / "auth.json"
            target = Path(directory) / "isolated" / ".codex" / "auth.json"
            source.parent.mkdir()
            source.write_text('{"access_token":"synthetic"}', encoding="utf-8")
            self.assertTrue(runner._copy_auth(source, target))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(target.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))

    def test_dry_run_does_not_require_auth_or_launch_codex(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = runner.main(["--dry-run"])
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["mode"], "dry-run")
        self.assertTrue(result["fixture_copies_identical"])
        self.assertFalse(result["arms"]["baseline"]["model_called"])
        self.assertEqual(result["arms"]["treatment"]["hooks_would_be_configured"], True)

    def test_treatment_installs_current_advisory_hooks_only_in_isolated_home(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "arm"
            hook_python = home / "python"
            runner._install_treatment_hooks(home, hook_python)
            config = json.loads((home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
            registered = [
                hook["command"]
                for event in ("UserPromptSubmit", "SubagentStart")
                for group in config["hooks"][event]
                for hook in group["hooks"]
            ]
            self.assertEqual(len(registered), 2)
            self.assertTrue(all("-m jevcompass hook" in command for command in registered))
            self.assertTrue((hook_python / "sitecustomize.py").is_file())

    def test_timeout_limit_is_enforced(self):
        with self.assertRaises(ValueError):
            runner.run_pair(mode="mock", model=None, timeout=runner.MAX_TIMEOUT + 1)


if __name__ == "__main__":
    unittest.main()
