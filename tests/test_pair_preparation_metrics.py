"""Prospective timing receipt tests for the test-order pair runner."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts" / "pilot_test_order_pair.py"
SPEC = importlib.util.spec_from_file_location("pilot_test_order_pair_timing_tests", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def advance_ms(self, amount):
        self.now += amount / 1000


class TestPairPreparationMetrics(unittest.TestCase):
    def run_timed_pair(self, root: Path, *, include_completion=True, validation_fields=None):
        clock = FakeClock()
        auth = root / "auth"
        auth.mkdir()
        output = root / "result"
        fake_time = types.SimpleNamespace(monotonic=clock.monotonic)
        original_chmod = runner.os.chmod

        setup_charged = False

        def chmod(path, mode, *args, **kwargs):
            nonlocal setup_charged
            result = original_chmod(path, mode, *args, **kwargs)
            if not setup_charged:
                setup_charged = True
                clock.advance_ms(100)
            return result

        auth_calls = 0

        def copy_auth(source, destination):
            nonlocal auth_calls
            auth_calls += 1
            clock.advance_ms(200 if auth_calls == 1 else 50)
            return True

        original_copy_fixture = runner._copy_identical_fixture

        def copy_fixture(source, destination):
            result = original_copy_fixture(source, destination)
            clock.advance_ms(300)
            return result

        arm_calls = []

        def run_arm(**kwargs):
            clock.advance_ms(400)
            fixture = kwargs["fixture"]
            path = fixture / runner.CHANGED_FILE
            text = path.read_text(encoding="utf-8")
            path.write_text(
                text.replace("weight_grams // 1000", "(weight_grams + 999) // 1000"),
                encoding="utf-8",
            )
            arm_calls.append(kwargs)
            result = {
                "cli_status": "completed",
                "failure": None,
                "focused_test_exits": [{"candidate_id": "unit", "exit_code": 0}],
                "required_suite_exit": 0,
                "independent_validation": {"status": "passed"},
                "token_usage_status": "unknown",
            }
            if validation_fields is not None:
                result.pop("independent_validation")
                result.update(validation_fields)
            if include_completion:
                # Agent wall time remains its pre-existing independent metric.
                result["completion_ms"] = 27.0
            return result

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(auth)}, clear=False), \
             mock.patch.object(runner, "time", fake_time), \
             mock.patch.object(runner.os, "chmod", side_effect=chmod), \
             mock.patch.object(runner.core, "_copy_auth", side_effect=copy_auth), \
             mock.patch.object(runner, "_copy_identical_fixture", side_effect=copy_fixture), \
             mock.patch.object(runner, "_run_arm", side_effect=run_arm):
            receipt = runner.run_pair(
                codex="/unused/mock-codex", model="gpt-6-luna",
                reasoning_effort="low", seed=1, output_dir=output,
            )
        return receipt, output, arm_calls

    def test_receipt_separates_shared_per_arm_and_execution_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt, output, _ = self.run_timed_pair(Path(temporary))
            self.assertTrue((output / "receipt.json").is_file())
        timing = receipt["timing"]
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(timing["clock"], "time.monotonic")
        self.assertEqual(timing["shared_setup_elapsed_ms"], 100.0)
        self.assertEqual(timing["shared_auth_preparation_elapsed_ms"], 200.0)
        self.assertIsNotNone(timing["fixture_parity_check_elapsed_ms"])
        self.assertEqual(
            set(timing["per_arm_fixture_auth_preparation_elapsed_ms"].values()),
            {350.0},
        )
        self.assertEqual(set(timing["per_arm_run_arm_elapsed_ms"].values()), {400.0})
        self.assertTrue(all(
            arm["completion_ms"] == 27.0
            and arm["timing"]["run_arm_elapsed_ms"] == 400.0
            and arm["timing"]["independent_validation_status"] == "included_in_run_arm_elapsed"
            and arm["timing"]["independent_validation_elapsed_ms"] is None
            for arm in receipt["arms"].values()
        ))
        self.assertTrue(all(
            status == "included_in_run_arm_elapsed"
            for status in timing["per_arm_independent_validation_status"].values()
        ))
        self.assertIsNone(timing["per_arm_independent_validation_elapsed_ms"]["arm-a"])
        self.assertGreaterEqual(timing["post_run_gate_validation_elapsed_ms"], 0.0)
        self.assertEqual(timing["total_pair_elapsed_ms"], 1800.0)
        self.assertIn("excludes wrapper postprocessing and serialization", timing["total_elapsed_scope"])

    def test_contract_wrapper_and_numeric_duration_are_reported(self):
        for fields in (
            {"independent_contract_validation": {"status": "passed"},
             "independent_validation_ms": 118.11},
            {"independent_validation_ms": 118.11},
        ):
            with self.subTest(fields=fields), tempfile.TemporaryDirectory() as temporary:
                receipt, _, _ = self.run_timed_pair(
                    Path(temporary), validation_fields=fields)
            for arm in receipt["arms"].values():
                self.assertEqual(arm["timing"]["independent_validation_status"],
                                 "included_in_run_arm_elapsed")
                self.assertEqual(arm["timing"]["independent_validation_elapsed_ms"], 118.11)

    def test_invalid_duration_without_validation_record_stays_unknown(self):
        for duration in (None, True, -1, float("nan"), float("inf"), "118"):
            with self.subTest(duration=duration), tempfile.TemporaryDirectory() as temporary:
                receipt, _, _ = self.run_timed_pair(
                    Path(temporary), validation_fields={"independent_validation_ms": duration})
            for arm in receipt["arms"].values():
                self.assertEqual(arm["timing"]["independent_validation_status"],
                                 "not_reported_by_runner")
                self.assertIsNone(arm["timing"]["independent_validation_elapsed_ms"])

    def test_missing_completion_stays_unavailable_and_unknown_is_not_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt, _, _ = self.run_timed_pair(Path(temporary), include_completion=False)
        self.assertEqual(receipt["status"], "completed")
        for arm in receipt["arms"].values():
            self.assertNotIn("completion_ms", arm)
            self.assertEqual(arm["timing"]["agent_completion_status"], "unavailable")
            self.assertEqual(arm["timing"]["run_arm_elapsed_ms"], 400.0)
            self.assertIsNone(arm["timing"]["independent_validation_elapsed_ms"])
        self.assertIsNone(runner._known_nonnegative_finite_ms(10**10000))
        self.assertIsNone(runner._known_nonnegative_finite_ms(None))
        self.assertIsNone(runner._known_nonnegative_finite_ms(-1))
        self.assertEqual(runner._known_nonnegative_finite_ms(12), 12.0)


if __name__ == "__main__":
    unittest.main()
