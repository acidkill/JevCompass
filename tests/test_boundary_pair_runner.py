"""Offline boundary runner contract checks."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from tests.test_pilot_test_order_pair import runner as base_runner

SPEC = importlib.util.spec_from_file_location(
    "boundary_runner", Path(__file__).parents[1] / "scripts" / "pilot_boundary_test_order_pair.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class BoundaryRunnerTests(unittest.TestCase):
    def test_engine_is_independent_and_task_is_correct(self):
        self.assertEqual(base_runner.CHANGED_FILE, "parcelquote/quote.py")
        self.assertEqual(runner.engine.CHANGED_FILE, "parcelquote/__main__.py")
        self.assertIn("integer minor_units", runner.engine.BASE_PROMPT)

    def test_false_agent_success_fails_independent_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fixture"
            shutil.copytree(runner.FIXTURE, root)
            with patch.object(runner, "_original_arm", return_value={"cli_status": "completed"}):
                result = runner._run_arm(fixture=root)
            self.assertEqual(result["cli_status"], "failed")
            self.assertEqual(result["independent_validation"]["unit"], 0)
            self.assertEqual(result["independent_validation"]["contract"], 1)
            self.assertTrue(result["immutable_files_preserved"])
            self.assertIsNone(result["first_useful_error_ms"])

    def test_frozen_hash_changes_for_modified_test(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fixture"
            shutil.copytree(runner.FIXTURE, root)
            before = runner.frozen_files(root)
            (root / "tests" / "test_unit_quote.py").write_text("tampered")
            self.assertNotEqual(before, runner.frozen_files(root))
