"""CLI integration for explicit post-change test ordering."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jevcompass.cli import main


class TestOrderCliTests(unittest.TestCase):
    def test_local_rank_preserves_mandatory_gate_without_executing_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "executed"
            source = Path(directory) / "input.json"
            source.write_text(json.dumps({
                "surface": "python",
                "candidates": [
                    {"id": "unit", "kind": "unit", "command": f"touch {marker}", "relevance": 0.5},
                    {"id": "contract", "kind": "contract", "command": "python -m unittest tests.test_contract", "relevance": 0.5},
                ],
                "required": [{"id": "ci", "command": "python -m unittest discover -s tests -v"}],
            }))
            output = io.StringIO()
            with mock.patch("jevcompass.test_order.DecisionsClient") as client, contextlib.redirect_stdout(output):
                client.return_value.decide.side_effect = TimeoutError()
                self.assertEqual(main(["tests", "rank", "--input", str(source), "--json"]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "no-remote-choice")
            self.assertEqual([item["id"] for item in result["ordered"]], ["unit", "contract"])
            self.assertEqual(result["required"], [{"id": "ci", "command": "python -m unittest discover -s tests -v"}])
            self.assertFalse(result["executed"])
            self.assertFalse(marker.exists())

    def test_rejects_oversized_input_without_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "too-large.json"
            source.write_text("x" * 65_537)
            with mock.patch("jevcompass.test_order.DecisionsClient") as client, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["tests", "rank", "--input", str(source)]), 2)
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
