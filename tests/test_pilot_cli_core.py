"""Offline fake-CLI tests for the bounded CLI core paired pilot runner."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_cli_core.py"
sys.path.insert(0, str(ROOT / "src"))
from jevcompass.advisor import classify_task
SPEC = importlib.util.spec_from_file_location("pilot_cli_core", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


class OrderedRandom:
    """Deterministically reverse each shuffled list for order assertions."""

    def shuffle(self, values):
        values.reverse()


def make_fake_codex(path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib\n"
        "home = pathlib.Path(os.environ['CODEX_HOME'])\n"
        "hooks = home / 'hooks.json'\n"
        "treatment = hooks.is_file()\n"
        "if treatment:\n"
        "    metric = home.parent / '.local/state/jevcompass/advisor.jsonl'\n"
        "    metric.parent.mkdir(parents=True, exist_ok=True)\n"
        "    status = 'local' if 'OPENROUTER_API_KEY' not in os.environ else 'key_leaked'\n"
        "    metric.write_text(json.dumps({'event':'UserPromptSubmit','category':'coding','status':status,'duration_ms':1.25,'trace':'abcdef12','prompt':'must-not-escape'}) + '\\n', encoding='utf-8')\n"
        "message = 'JevCompass advice ID: abcdef12\\nSynthetic completion.' if treatment else 'Synthetic completion.'\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':message}}), flush=True)\n"
        "print(json.dumps({'type':'item.started','item':{'type':'command_execution','name':'exec_command','command':'DO NOT RETAIN'}}), flush=True)\n"
        "print(json.dumps({'type':'turn.completed'}), flush=True)\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path


class PilotCliCoreTests(unittest.TestCase):
    def setUp(self):
        if not runner.FIXTURE.is_dir():
            self.skipTest("independently prepared cli_core fixture is not present yet")

    def test_mock_pair_randomizes_and_emits_safe_metadata_only(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = make_fake_codex(Path(directory) / "fake-codex")
            result = runner.run_pilot(
                mode="mock", model="test-model", reasoning_effort="high",
                timeout=4, cases=("P01", "R01"), codex=str(fake),
                rng=OrderedRandom(),
            )
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["preflight"])
        self.assertEqual(result["case_order"], ["R01", "P01"])
        self.assertFalse(result["openrouter_key_forwarded"])
        for case_id, case in result["cases"].items():
            self.assertTrue(case["fixture_copies_identical"])
            self.assertRegex(case["source_sha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(case["arm_order"], ["treatment", "baseline"])
            self.assertEqual(set(case["arms"]), {"baseline", "treatment"})
            self.assertEqual(case["sandbox"], runner._case_settings(case_id)["sandbox"])
            self.assertIsNone(case["arms"]["baseline"].get("advice_id"))
            self.assertEqual(case["arms"]["treatment"]["advice_id"], "abcdef12")
            self.assertTrue(case["arms"]["treatment"]["advice_id_before_first_tool"])
            self.assertEqual(case["arms"]["treatment"]["advice_metric"]["metric"]["trace"], "abcdef12")
            self.assertIsNone(case["arms"]["treatment"]["first_useful_action_ms"])
        rendered = json.dumps(result)
        for forbidden in ("Synthetic completion", "DO NOT RETAIN", "must-not-escape", "command\":"):
            self.assertNotIn(forbidden, rendered)
        for case_id in ("P01", "R01"):
            command = runner._build_command(
                codex="fake", model="test-model", reasoning_effort="high",
                case_id=case_id,
            )
            self.assertIn("--sandbox", command)
            self.assertIn(runner._case_settings(case_id)["sandbox"], command)
            self.assertEqual(command[:4], ["fake", "-a", "never", "exec"])
            self.assertIn("never", command)
            self.assertIn("--model", command)
            self.assertIn("test-model", command)
            self.assertIn("model_reasoning_effort=high", command)

    def test_preflight_appends_same_instruction_without_changing_substantive_classification(self):
        for case_id in sorted(runner.PREFLIGHT_CASES):
            base = runner._build_command(
                codex="fake", model="test-model", reasoning_effort="high", case_id=case_id,
            )[-1]
            preflight = runner._build_command(
                codex="fake", model="test-model", reasoning_effort="high", case_id=case_id,
                preflight=True,
            )[-1]
            self.assertEqual(preflight, f"{base}\n\n{runner.PREFLIGHT_INSTRUCTION}")
            self.assertEqual(classify_task(preflight), classify_task(base), case_id)

    def test_preflight_preserves_exact_routine_prompts_and_does_not_trigger_classification(self):
        for case_id in sorted(runner.ROUTINE_CASES):
            command = runner._build_command(
                codex="fake", model="test-model", reasoning_effort="high", case_id=case_id,
                preflight=True,
            )
            self.assertEqual(command[-1], runner.PROMPTS[case_id])
            self.assertIsNone(classify_task(command[-1]), case_id)

    def test_preflight_receipt_is_recorded_in_dry_run(self):
        result = runner.run_pilot(
            mode="dry-run", model=None, cases=("P01", "R01"),
            preflight=True, rng=OrderedRandom(),
        )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["preflight"])

    def test_event_parser_distinguishes_first_tool_from_first_useful_action(self):
        start = time.monotonic()
        lines = [
            json.dumps({"type": "item.completed", "item": {"type": "error", "message": "ignored"}}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "JevCompass advice ID: 1234abcd"}}),
            json.dumps({"type": "item.started", "item": {"type": "command_execution", "name": "exec_command", "command": "secret command"}}),
        ]
        parsed = runner.parse_event_stream(lines, start_monotonic=start, event_times=[start, start + .1, start + .3])
        self.assertEqual(parsed["first_tool"]["order"], 3)
        self.assertEqual(parsed["first_tool"]["elapsed_ms"], 300.0)
        self.assertTrue(parsed["advice_id_before_first_tool"])
        self.assertNotIn("secret command", json.dumps(parsed))

    def test_event_parser_reports_heuristic_source_read_time_without_retaining_command_or_text(self):
        start = time.monotonic()
        assistant_texts = []
        lines = [
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "A private response."}}),
            json.dumps({"type": "item.started", "item": {"type": "command_execution", "name": "exec_command", "command": "sed -n '1,20p' README.md"}}),
        ]
        parsed = runner.parse_event_stream(
            lines, start_monotonic=start, event_times=[start, start + .2],
            assistant_text_sink=assistant_texts,
        )
        self.assertEqual(parsed["first_source_read_ms"], 200.0)
        self.assertEqual(assistant_texts, ["A private response."])
        rendered = json.dumps(parsed)
        self.assertNotIn("README.md", rendered)
        self.assertNotIn("A private response", rendered)

    def test_event_parser_keeps_only_sandboxed_command_check_and_exit_code(self):
        start = time.monotonic()
        lines = [json.dumps({
            "type": "item.completed",
            "item": {"type": "command_execution", "command": "python -m unittest discover -s tests; secret-token", "exit_code": 0},
        })]
        parsed = runner.parse_event_stream(lines, start_monotonic=start, event_times=[start])
        self.assertEqual(parsed["events"][0]["command_check"], "fixture_tests")
        self.assertEqual(parsed["events"][0]["exit_code"], 0)
        self.assertNotIn("secret-token", json.dumps(parsed))

    def test_p03_checks_syntax_and_unset_guard_without_running_script(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            (fixture / "scripts").mkdir()
            script = fixture / "scripts" / "render_report.sh"
            script.write_text(
                '#!/usr/bin/env bash\nset -u\nprintf "%s\\n" "$OUTPUT_PATH"\ntouch SHOULD_NOT_EXIST\n',
                encoding="utf-8",
            )
            checks = runner._fixture_outcome_checks("P03", fixture, None)
            self.assertEqual(checks, {"bash_syntax": True, "no_unset_output_path_defect": False})
            self.assertFalse((fixture / "SHOULD_NOT_EXIST").exists())
            script.write_text(
                '#!/usr/bin/env bash\nset -u\n: "${OUTPUT_PATH:?required}"\nprintf "%s\\n" "$OUTPUT_PATH"\n',
                encoding="utf-8",
            )
            checks = runner._fixture_outcome_checks("P03", fixture, None)
            self.assertEqual(checks, {"bash_syntax": True, "no_unset_output_path_defect": True})

    def test_p03_argument_based_output_does_not_require_output_path_variable(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            (fixture / "scripts").mkdir()
            (fixture / "scripts" / "render_report.sh").write_text(
                '#!/usr/bin/env bash\nset -euo pipefail\nprintf "%s\\n" "${1:?output required}"\n',
                encoding="utf-8",
            )
            self.assertEqual(
                runner._fixture_outcome_checks("P03", fixture, None),
                {"bash_syntax": True, "no_unset_output_path_defect": True},
            )

    def test_fixture_answer_indicators_are_limited_and_unknown_without_text(self):
        fixture = runner.FIXTURE
        self.assertIsNone(runner._answer_indicator("R01", None, fixture))
        self.assertTrue(runner._answer_indicator("R01", "Branch: fixture-main", fixture))
        count = sum(path.is_file() for path in fixture.iterdir())
        self.assertTrue(runner._answer_indicator("R02", f"There are {count} top-level files.", fixture))
        self.assertTrue(runner._answer_indicator("R03", "README.md exists.", fixture))
        size = (fixture / "pyproject.toml").stat().st_size
        self.assertTrue(runner._answer_indicator("R04", f"pyproject.toml is {size} bytes.", fixture))
        self.assertTrue(runner._answer_indicator("R05", "There are no top-level Python files.", fixture))
        self.assertTrue(runner._answer_indicator("R06", "README.md contains timeout.", fixture))

    def test_p07_requires_basic_contract_indicators_and_reports_unknown_without_text(self):
        self.assertEqual(
            runner._fixture_outcome_checks("P07", runner.FIXTURE, None),
            {"contract_indicators": None},
        )
        good = "POST /status accepts required and optional inputs, returns a validated status response, and handles invalid input and server errors."
        bad = "POST /status returns a result."
        self.assertEqual(runner._fixture_outcome_checks("P07", runner.FIXTURE, good), {"contract_indicators": True})
        self.assertEqual(runner._fixture_outcome_checks("P07", runner.FIXTURE, bad), {"contract_indicators": False})

    def test_p01_p05_never_execute_modified_fixture_python(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture"
            marker = root / "executed"
            (fixture / "tests").mkdir(parents=True)
            (fixture / "tinytext").mkdir()
            (fixture / "tests" / "test_unsafe.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
                encoding="utf-8",
            )
            (fixture / "README.md").write_text(
                "Install with python -m pip install .\nRun python -m tinytext --help.\n"
                "Run python -m unittest discover -s tests.\n", encoding="utf-8",
            )
            (fixture / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
            (fixture / "tinytext" / "cli.py").write_text(
                "parser.add_argument('--check')\nparser.parse_args()\n", encoding="utf-8",
            )
            (fixture / "tinytext" / "__main__.py").write_text("main()\n", encoding="utf-8")

            self.assertEqual(
                runner._fixture_outcome_checks("P01", fixture, None),
                {"focused_unittest_exit": None},
            )
            self.assertEqual(
                runner._fixture_outcome_checks("P05", fixture, None),
                {"install_instruction_coherent": True, "help_instruction_coherent": True,
                 "help_command_exit": None, "test_instruction_exit": None},
            )
            self.assertFalse(marker.exists())
            sandbox_events = [
                {"command_check": "fixture_tests", "exit_code": 0},
                {"command_check": "cli_help", "exit_code": 0},
            ]
            self.assertEqual(
                runner._fixture_outcome_checks("P01", fixture, None, sandbox_events),
                {"focused_unittest_exit": True},
            )
            self.assertEqual(
                runner._fixture_outcome_checks("P05", fixture, None, sandbox_events)["test_instruction_exit"],
                True,
            )
            self.assertFalse(marker.exists())

    def test_routine_controls_are_read_only_and_case_sandbox_is_explicit(self):
        self.assertEqual(runner.READ_ONLY_CASES, {"P07", "R01", "R02", "R03", "R04", "R05", "R06"})
        self.assertEqual({case for case in runner.CASE_IDS if runner._case_settings(case)["sandbox"] == "workspace-write"}, {"P01", "P03", "P05"})
        self.assertEqual(runner.ROUTINE_CASES, {"R01", "R02", "R03", "R04", "R05", "R06"})

    def test_isolated_environment_drops_credentials_and_proxy_variables(self):
        prior = {key: os.environ.get(key) for key in ("OPENROUTER_API_KEY", "AWS_SECRET_ACCESS_KEY", "HTTPS_PROXY")}
        os.environ["OPENROUTER_API_KEY"] = "synthetic-secret"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "synthetic-secret"
        os.environ["HTTPS_PROXY"] = "https://proxy.invalid"
        try:
            with tempfile.TemporaryDirectory() as directory:
                env = runner._isolated_environment(home=Path(directory), isolated_python=Path(directory) / "python")
        finally:
            for key, value in prior.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("HTTPS_PROXY", env)

    def test_dry_run_never_calls_model_and_pairs_all_selected_cases(self):
        result = runner.run_pilot(mode="dry-run", model=None, cases=("P07", "R06"), rng=OrderedRandom())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["case_order"], ["R06", "P07"])
        for case in result["cases"].values():
            self.assertTrue(case["fixture_copies_identical"])
            self.assertTrue(all(not arm["model_called"] for arm in case["arms"].values()))

    def test_bounds_reject_invalid_timeouts_cases_and_effort(self):
        with self.assertRaises(ValueError):
            runner.run_pilot(mode="mock", model="fake", timeout=runner.MAX_TIMEOUT + 1)
        with self.assertRaises(ValueError):
            runner.run_pilot(mode="mock", model="fake", cases=("P02",))
        with self.assertRaises(ValueError):
            runner.run_pilot(mode="mock", model="fake", reasoning_effort="ultra")

    def test_event_collector_times_out_and_kills_fake_process(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            stdout=subprocess.PIPE,
        )
        lines, times, failure = runner._collect_events(process, started=time.monotonic(), timeout=1)
        self.assertEqual((lines, times, failure), ([], [], "timeout"))
        self.assertIsNotNone(process.poll())


if __name__ == "__main__":
    unittest.main()
