"""Ensure the fictional test-order pilot starts with a real, repairable failure."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "coding_test_order"
REQUIRED = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]


class CodingTestOrderFixtureTests(unittest.TestCase):
    def test_seeded_boundary_fails_and_reference_repair_passes_full_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "parcelquote"
            shutil.copytree(FIXTURE, root)
            before = subprocess.run(REQUIRED, cwd=root, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(before.returncode, 0)
            self.assertIn("test_partial_kilogram_rounds_up", before.stderr)
            self.assertIn("test_partial_kilogram_json_contract", before.stderr)
            source = root / "parcelquote" / "quote.py"
            current = source.read_text(encoding="utf-8")
            self.assertIn("weight_grams // 1000", current)
            source.write_text(current.replace("weight_grams // 1000", "(weight_grams + 999) // 1000"), encoding="utf-8")
            after = subprocess.run(REQUIRED, cwd=root, capture_output=True, text=True, timeout=15)
            self.assertEqual(after.returncode, 0, after.stderr)
            self.assertIn("Ran 6 tests", after.stderr)

    def test_machine_readable_candidates_match_visible_required_commands(self):
        import json
        data = json.loads((FIXTURE / "test-options.json").read_text(encoding="utf-8"))
        self.assertEqual(data["surface"], "python")
        self.assertEqual({item["kind"] for item in data["candidates"]}, {"unit", "contract"})
        self.assertEqual([item["command"] for item in data["required"]],
                         ["python -m unittest discover -s tests -v"])
        visible = (FIXTURE / "TESTING.md").read_text(encoding="utf-8")
        for item in (*data["candidates"], *data["required"]):
            self.assertIn(item["command"], visible)


if __name__ == "__main__":
    unittest.main()
