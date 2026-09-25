"""Guard the synthetic ambiguous-import triage fixture contract."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parent / "fixtures" / "ambiguous_import_triage"


class AmbiguousImportTriageFixtureTests(unittest.TestCase):
    def test_seeded_focused_failure_is_stable_and_identifying(self):
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests",
             "-p", "test_store.py", "-v"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERROR: test_store (unittest.loader._FailedTest.test_store)", result.stdout)
        self.assertIn("No module named 'parcelcache.codec'", result.stdout)

    def test_stdlib_spec_probe_discriminates_local_path_drift(self):
        probe = (
            "import importlib.util; "
            "print('package', importlib.util.find_spec('parcelcache') is not None); "
            "print('old', importlib.util.find_spec('parcelcache.codec') is not None); "
            "print('current', importlib.util.find_spec('parcelcache.wire') is not None)"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "package True",
            "old False",
            "current True",
        ])


if __name__ == "__main__":
    unittest.main()
