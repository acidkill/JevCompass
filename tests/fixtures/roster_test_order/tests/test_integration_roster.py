import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class RosterCliIntegrationTests(unittest.TestCase):
    def test_cli_emits_roster_json_contract(self):
        run = subprocess.run(
            [sys.executable, "-m", "roster", "--input", "tests/data/roster.json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(
            json.loads(run.stdout),
            {
                "count": 2,
                "roster": [
                    {"email": "alice@example.test", "name": "Alice", "team": "Data"},
                    {"email": "zoe@example.test", "name": "Zoe", "team": "Ops"},
                ],
            },
        )

    def test_cli_rejects_non_array_without_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps({"email": "x@example.test"}), encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-m", "roster", "--input", str(path)],
                capture_output=True,
                text=True,
                timeout=5,
            )
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, "")
        self.assertIn("input must be a JSON array", run.stderr)


if __name__ == "__main__":
    unittest.main()
