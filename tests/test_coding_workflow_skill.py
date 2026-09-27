"""Verify the new coding-workflow CLI examples against the local CLI safely."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import re
import shlex
import tempfile
import unittest
from unittest import mock

from jevcompass.cli import main


SKILL = (Path(__file__).parents[1] / "src/jevcompass/bundled_skills/"
         "jevcompass-coding-workflow/SKILL.md")


def _shell_example(prefix: str) -> list[str]:
    text = SKILL.read_text(encoding="utf-8")
    blocks = re.findall(r"```sh\s*(.*?)```", text, flags=re.DOTALL)
    for block in blocks:
        normalized = block.replace("\\\n", " ")
        if normalized.strip().startswith(prefix):
            argv = shlex.split(normalized)
            return argv[1:]  # remove the documented `jevcompass` executable
    raise AssertionError(f"missing shell example: {prefix}")


class CodingWorkflowSkillCliExamplesTests(unittest.TestCase):
    def test_strategy_example_uses_current_enum_cli_without_exposing_free_text(self):
        args = _shell_example("jevcompass strategy choose")
        with mock.patch("jevcompass.strategy.DecisionsClient") as client, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            client.return_value.decide.return_value = {
                "strategy": {
                    "type": "choice",
                    "choice": "inspect_dependency_or_symbol_use",
                    "confidence": 0.9,
                },
            }
            self.assertEqual(main(args), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "remote-choice")
        request_state, request_questions = client.return_value.decide.call_args.args
        serialized = json.dumps((request_state, request_questions))
        self.assertNotIn("source", serialized)
        self.assertNotIn("prompt", serialized)

    def test_rank_example_has_valid_metadata_preserves_required_gate_and_never_runs_checks(self):
        text = SKILL.read_text(encoding="utf-8")
        match = re.search(r"```json\s*(.*?)```", text, flags=re.DOTALL)
        self.assertIsNotNone(match)
        metadata = json.loads(match.group(1))
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "test-order.json"
            input_path.write_text(json.dumps(metadata), encoding="utf-8")
            args = _shell_example("jevcompass tests rank")
            args[args.index("--input") + 1] = str(input_path)
            with mock.patch("jevcompass.test_order.DecisionsClient") as client, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                client.return_value.decide.return_value = {
                    "first": {"type": "choice", "choice": "t1", "confidence": 0.9},
                }
                self.assertEqual(main(args), 0)
            result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "remote-choice")
        self.assertFalse(result["executed"])
        self.assertEqual(result["required"], metadata["required"])
        state, _questions = client.return_value.decide.call_args.args
        serialized = json.dumps(state)
        for candidate in metadata["candidates"]:
            self.assertNotIn(candidate["id"], serialized)
            self.assertNotIn(candidate["command"], serialized)

    def test_triage_example_uses_observed_exit_and_enum_only_hypotheses(self):
        args = _shell_example("jevcompass triage")
        with mock.patch("jevcompass.triage.DecisionsClient") as client, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            client.return_value.decide.return_value = {
                "diagnostic": {
                    "type": "choice",
                    "choice": "import_path_changed",
                    "confidence": 0.9,
                },
            }
            self.assertEqual(main(args), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "remote-choice")
        self.assertEqual(result["observed_exit_status"], 1)
        self.assertTrue(result["test_failed"])
        self.assertFalse(result["executed"])
        state, questions = client.return_value.decide.call_args.args
        serialized = json.dumps((state, questions))
        self.assertNotIn("traceback", serialized)
        self.assertNotIn("exception", serialized)


if __name__ == "__main__":
    unittest.main()
