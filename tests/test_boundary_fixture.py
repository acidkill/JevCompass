"""Freeze the public boundary task and guard meaningful validation."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

FIXTURE = Path(__file__).parent / "fixtures" / "boundary_test_order"


class BoundaryFixtureTests(unittest.TestCase):
    def run_suite(self, root, pattern):
        return subprocess.run([sys.executable, "-m", "unittest", "discover",
                               "-s", "tests", "-p", pattern, "-v"],
                              cwd=root, capture_output=True, text=True, timeout=10)

    def test_original_calculation_passes_but_public_mapping_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fixture"
            shutil.copytree(FIXTURE, root)
            self.assertEqual(self.run_suite(root, "test_unit*.py").returncode, 0)
            contract = self.run_suite(root, "test_contract*.py")
            self.assertEqual(contract.returncode, 1)
            self.assertIn("test_money_representation_json_contract", contract.stderr)

    def test_correct_mapping_satisfies_full_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fixture"
            shutil.copytree(FIXTURE, root)
            source = root / "parcelquote" / "__main__.py"
            text = source.read_text()
            source.write_text(text.replace(
                '"total_cents": quote.total_cents,',
                '"total_cents": quote.total_cents,\n'
                '        "money": {"currency": "USD", "minor_units": quote.total_cents},'))
            run = self.run_suite(root, "test*.py")
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_metadata_preserves_mandatory_suite_and_unknown_runtime(self):
        data = json.loads((FIXTURE / "test-options.json").read_text())
        self.assertEqual([v["coverage"] for v in data["candidates"]], ["indirect", "direct"])
        self.assertTrue(all(v["runtime"] == "unknown" for v in data["candidates"]))
        self.assertEqual(data["required"][0]["command"],
                         "python -m unittest discover -s tests -v")
