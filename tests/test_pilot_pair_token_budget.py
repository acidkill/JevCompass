"""Offline propagation tests for optional per-arm pair token budgets."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import pilot_pretask_strategy_pair as pretask  # noqa: E402
import pilot_cross_layer_test_order_pair as cross_layer  # noqa: E402
import pilot_target_coverage_test_order_pair as target_coverage  # noqa: E402


class PairTokenBudgetTests(unittest.TestCase):
    def test_shared_pair_passes_cap_to_both_randomized_arms_and_records_limit(self):
        arm_calls = []

        def fake_arm(**kwargs):
            arm_calls.append(kwargs)
            return {
                "cli_status": "completed", "token_usage_status": "unscored",
                "token_usage": None, "focused_test_exits": [{"exit_code": 0}],
                "required_suite_exit": 0, "failure": None,
            }

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "receipt"
            with mock.patch.dict(os.environ, {"CODEX_HOME": directory}), \
                 mock.patch.object(cross_layer.engine.core, "_copy_auth", return_value=True), \
                 mock.patch.object(cross_layer.engine, "_copy_identical_fixture", return_value="a" * 64), \
                 mock.patch.object(cross_layer.engine, "_run_arm", side_effect=fake_arm), \
                 mock.patch.object(cross_layer.engine, "_safe_final_artifact", return_value=None):
                receipt = cross_layer._original_pair(
                    codex="unused", model="gpt-6-luna", reasoning_effort="low",
                    output_dir=output, fixture_source=cross_layer.FIXTURE,
                    seed=42, max_tokens=1234,
                )

        self.assertEqual(len(arm_calls), 2)
        self.assertEqual([call["max_tokens"] for call in arm_calls], [1234, 1234])
        self.assertEqual(receipt["max_tokens_per_arm"], 1234)
        self.assertIn("not a provider per-request hard cap", receipt["token_budget_usage_limitation"])
        self.assertEqual({arm["token_budget_status"] for arm in receipt["arms"].values()}, {"usage_unknown"})

    def test_shared_pair_default_does_not_pass_cap_or_add_receipt_metadata(self):
        arm_calls = []

        def fake_arm(**kwargs):
            arm_calls.append(kwargs)
            return {"cli_status": "failed", "focused_test_exits": [], "required_suite_exit": None}

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ, {"CODEX_HOME": directory}), \
                 mock.patch.object(cross_layer.engine.core, "_copy_auth", return_value=True), \
                 mock.patch.object(cross_layer.engine, "_copy_identical_fixture", return_value="b" * 64), \
                 mock.patch.object(cross_layer.engine, "_run_arm", side_effect=fake_arm), \
                 mock.patch.object(cross_layer.engine, "_safe_final_artifact", return_value=None):
                receipt = cross_layer._original_pair(
                    codex="unused", model="gpt-6-luna", reasoning_effort="low",
                    output_dir=Path(directory) / "receipt", fixture_source=cross_layer.FIXTURE,
                )

        self.assertEqual(len(arm_calls), 2)
        self.assertTrue(all("max_tokens" not in call for call in arm_calls))
        self.assertNotIn("max_tokens_per_arm", receipt)
        self.assertNotIn("token_budget_usage_limitation", receipt)

    def test_pretask_wrapper_forwards_cap_in_both_arm_calls(self):
        collector_calls = []

        def fake_collect(process, **kwargs):
            collector_calls.append(kwargs)
            return [], [], None

        def fake_prepare(home, allow_key):
            return {"status": "no-remote-choice", "candidate_ids": []}, None

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture"
            fixture.mkdir()
            home = Path(directory) / "home"
            base = {
                "codex": "unused", "model": "gpt-6-luna", "reasoning_effort": "low",
                "fixture": fixture, "home": home, "timeout": 10,
                "allow_openrouter_key": False, "max_tokens": 321,
            }
            with mock.patch.object(pretask, "_prepare", side_effect=fake_prepare), \
                 mock.patch.object(pretask.engine.core.subprocess, "Popen",
                                   return_value=SimpleNamespace(returncode=0)), \
                 mock.patch.object(pretask.engine.core, "_collect_events", side_effect=fake_collect):
                pretask._run_arm(**{**base, "prompt": pretask.engine.BASE_PROMPT})
                pretask._run_arm(**{**base, "prompt": pretask.engine.BASE_PROMPT + pretask.MARKER})

        self.assertEqual(len(collector_calls), 2)
        self.assertEqual([call["max_tokens"] for call in collector_calls], [321, 321])

    def test_cross_layer_custom_collector_receives_cap_and_default_omits_it(self):
        observed = []

        class FakeProcess:
            returncode = 0

        def fake_live(process, **kwargs):
            observed.append(kwargs)
            return [], [], None

        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture"
            fixture.mkdir()
            common = {
                "codex": "unused", "model": "gpt-6-luna", "reasoning_effort": "low",
                "prompt": "baseline", "fixture": fixture,
                "home": Path(directory) / "home", "timeout": 2,
                "allow_openrouter_key": False,
            }
            with mock.patch.object(cross_layer, "_digest_source", return_value="c" * 64), \
             mock.patch.object(cross_layer, "_frozen_files", return_value={}), \
             mock.patch.object(cross_layer, "_independent_final_validation", return_value={"status": "passed"}), \
             mock.patch.object(cross_layer, "_collect_events_live", side_effect=fake_live), \
             mock.patch.object(cross_layer.engine.core.subprocess, "Popen",
                               return_value=FakeProcess()):
                for cap in (321, None):
                    kwargs = dict(common)
                    kwargs["home"] = Path(directory) / f"home-{cap}"
                    if cap is not None:
                        kwargs["max_tokens"] = cap
                    cross_layer._run_arm(**kwargs)

        self.assertEqual(observed[0]["max_tokens"], 321)
        self.assertNotIn("max_tokens", observed[1])

    def test_target_coverage_cli_wrapper_forwards_cap(self):
        observed = {}

        def fake_prepare(destination):
            destination.mkdir()
            return destination

        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(target_coverage, "_prepare_enriched_fixture", side_effect=fake_prepare), \
             mock.patch.object(target_coverage, "cross_layer") as cross:
            cross.run_pair.side_effect = lambda **kwargs: observed.update(kwargs) or {"status": "failed"}
            result = target_coverage.run_pair(
                codex="unused", model="gpt-6-luna", reasoning_effort="low",
                output_dir=Path(directory) / "out", max_tokens=987,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(observed["max_tokens"], 987)
        self.assertEqual(observed["advice_policy"], "nonbinding")

    def test_custom_collector_stops_after_completed_usage_crosses_cap(self):
        usage_event = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 4, "output_tokens": 0,
                "cached_input_tokens": 0, "cache_write_input_tokens": 0,
                "reasoning_output_tokens": 0,
            },
        }
        payload = json.dumps(usage_event)
        code = (
            "import sys,time; "
            f"payload={payload!r}; split={len(payload) // 2}; "
            "sys.stdout.write(payload[:split]); sys.stdout.flush(); "
            "time.sleep(0.05); sys.stdout.write(payload[split:] + '\\n'); "
            "sys.stdout.flush(); time.sleep(1)"
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            process = subprocess.Popen(
                [sys.executable, "-c", code], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            lines, _, failure = cross_layer._collect_events_live(
                process, started=__import__("time").monotonic(), timeout=8,
                preserve_on_failure=True, fixture=fixture,
                before_source=None, observation={}, max_tokens=1,
            )

        self.assertEqual(failure, "token_budget_exceeded")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], payload)
        self.assertIsNotNone(process.poll())

    def test_shared_cli_help_exposes_optional_cap(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            cross_layer.engine.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("--max-tokens", output.getvalue())

    def test_target_coverage_cli_help_exposes_optional_cap(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            target_coverage.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("--max-tokens", output.getvalue())


if __name__ == "__main__":
    unittest.main()
