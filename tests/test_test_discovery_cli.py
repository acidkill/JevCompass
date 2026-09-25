"""CLI proof that discovery and ranking stay local until safe metadata choice."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jevcompass.cli import main


class DiscoverCliTests(unittest.TestCase):
    def test_discovers_two_kinds_without_running_required_or_focused_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "src").mkdir()
            (root / "src" / "widget.py").write_text("value = 1\n")
            (root / "tests" / "integration").mkdir(parents=True)
            (root / "tests" / "test_widget.py").write_text("import unittest\n")
            (root / "tests" / "integration" / "test_widget.py").write_text("import unittest\n")
            marker = root / "should-not-exist"
            required = f"touch {marker}"
            output = io.StringIO()
            with mock.patch("jevcompass.test_order.DecisionsClient") as client, contextlib.redirect_stdout(output):
                client.return_value.decide.return_value = {
                    "first": {"type": "choice", "choice": "t2", "confidence": 0.9}
                }
                self.assertEqual(main(["tests", "discover", "--repo", str(root),
                                       "--required", required, "--json"]), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "remote-choice")
            self.assertEqual({item["kind"] for item in payload["ordered"]}, {"unit", "integration"})
            self.assertEqual(payload["required"][0]["command"], required)
            self.assertFalse(payload["executed"])
            self.assertFalse(marker.exists())
            request = repr(client.return_value.decide.call_args)
            self.assertNotIn(str(root), request)
            self.assertNotIn("test_widget.py", request)
            self.assertNotIn(required, request)


if __name__ == "__main__":
    unittest.main()
