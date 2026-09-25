"""Offline safety and event-accounting tests for the coding test-order runner."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_test_order_pair.py"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("pilot_test_order_pair", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


def command_event(event_type: str, identifier: str, command: str, **extra):
    return json.dumps({
        "type": event_type,
        "item": {
            "id": identifier,
            "type": "command_execution",
            "command": command,
            **extra,
        },
    })


def fake_events(prompt: str, *, choice_payload: str | None = None, required_id_match=True):
    treatment = "After your code edit" in prompt
    events = []
    if treatment:
        order = ["contract"]
        choice = choice_payload or json.dumps({
            "status": "remote-choice",
            "ordered": [
                {"id": "contract", "kind": "contract", "command": runner.CONTRACT_COMMAND},
                {"id": "unit", "kind": "unit", "command": runner.UNIT_COMMAND},
            ],
            "required": [{"id": "full", "command": runner.REQUIRED_COMMAND}],
            "executed": False,
        })
        rank_command = "python -m jevcompass tests rank --input test-options.json --json"
        events.extend([
            command_event("item.started", "rank-1", rank_command),
            command_event("item.completed", "rank-1", rank_command, exit_code=0, aggregated_output=choice),
        ])
    else:
        order = ["unit"]
    for index, candidate in enumerate(order):
        command = getattr(runner, f"{candidate.upper()}_COMMAND")
        identifier = f"focused-{index}"
        wrapped = f"bash -lc {json.dumps(command)}"
        events.extend([
            command_event("item.started", identifier, wrapped),
            command_event("item.completed", identifier, wrapped, exit_code=0),
        ])
    required_id = "required-1" if required_id_match else "different-id"
    required = f"bash -lc {json.dumps(runner.REQUIRED_COMMAND)}"
    events.extend([
        command_event("item.started", "required-1", required),
        command_event("item.completed", required_id, required, exit_code=0),
        json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 12, "cached_input_tokens": 2,
            "cache_write_input_tokens": 0, "output_tokens": 3,
            "reasoning_output_tokens": 1,
        }}),
    ])
    lines = [event for event in events]
    base = time.monotonic()
    times = [base + index * 0.0001 for index in range(len(lines))]
    return lines, times, None


class FakeProcess:
    returncode = 0

    def __init__(self, prompt: str):
        self.prompt = prompt


class TestOrderPairRunnerTests(unittest.TestCase):
    def setup_run(self, temp: str, *, key: bool = False):
        root = Path(temp)
        auth_home = root / "auth"
        auth_home.mkdir()
        (auth_home / "auth.json").write_text('{"access_token":"TOP_SECRET_TOKEN"}', encoding="utf-8")
        output = root / "out"
        calls = []
        real_popen = runner.subprocess.Popen

        def popen(command, *, cwd=None, env=None, **kwargs):
            if command[0] == "git":
                return real_popen(command, cwd=cwd, env=env, **kwargs)
            prompt = command[-1]
            treatment = "After your code edit" in prompt
            fixture = Path(cwd)
            initial_fixture_hash = runner.core.fixture_digest(fixture)
            initial = (fixture / runner.CHANGED_FILE).read_text(encoding="utf-8")
            (fixture / runner.CHANGED_FILE).write_text(
                initial.replace("weight_grams // 1000", "(weight_grams + 999) // 1000"),
                encoding="utf-8",
            )
            auth_file = Path(env["CODEX_HOME"]) / "auth.json"
            calls.append({
                "prompt": prompt,
                "cwd": str(cwd),
                "env": dict(env),
                "fixture_hash": initial_fixture_hash,
                "fixture_has_git": (fixture / ".git").exists(),
                "auth_bytes": auth_file.read_bytes(),
                "auth_mode": stat.S_IMODE(auth_file.stat().st_mode),
                "codex_files": sorted(path.name for path in Path(env["CODEX_HOME"]).iterdir()),
                "treatment": treatment,
            })
            return FakeProcess(prompt)

        return auth_home, output, calls, popen

    def test_pair_parity_auth_isolation_privacy_and_required_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            auth_home, output, calls, popen = self.setup_run(temp)
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(auth_home)}, clear=False), \
                 mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "OPENROUTER_SECRET"}), \
                 mock.patch.object(runner.subprocess, "Popen", side_effect=popen), \
                 mock.patch.object(
                     runner.core, "_collect_events",
                     side_effect=lambda process, **kwargs: fake_events(process.prompt),
                 ):
                receipt = runner.run_pair(
                    codex="/fake/codex", model="gpt-5.6", reasoning_effort="high",
                    seed=1, output_dir=output, allow_openrouter_key=False,
                )

            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["fixture_hash"], calls[1]["fixture_hash"])
            self.assertFalse(calls[0]["fixture_has_git"])
            self.assertFalse(calls[1]["fixture_has_git"])
            self.assertEqual(calls[0]["auth_bytes"], calls[1]["auth_bytes"])
            self.assertEqual(calls[0]["auth_mode"], 0o600)
            self.assertEqual(calls[1]["auth_mode"], 0o600)
            self.assertNotEqual(calls[0]["env"]["CODEX_HOME"], calls[1]["env"]["CODEX_HOME"])
            self.assertNotIn("OPENROUTER_API_KEY", calls[0]["env"])
            self.assertNotIn("OPENROUTER_API_KEY", calls[1]["env"])
            self.assertEqual(calls[0]["codex_files"], ["auth.json"])
            self.assertEqual(calls[1]["codex_files"], ["auth.json"])
            self.assertEqual(receipt["status"], "completed")
            self.assertTrue(all(arm["required_suite_exit"] == 0 for arm in receipt["arms"].values()))
            self.assertTrue(all(arm["required_suite_invocation_observed"] for arm in receipt["arms"].values()))
            treatment = next(arm for arm in receipt["arms"].values()
                             if arm["choice"]["status"] == "remote-choice")
            self.assertEqual(treatment["focused_test_exits"], [{"candidate_id": "contract", "exit_code": 0}])
            self.assertTrue(treatment["choice_latency_ms"] is not None)
            self.assertGreaterEqual(treatment["completion_ms"], treatment["choice_latency_ms"])
            public = (output / "receipt.json").read_text(encoding="utf-8")
            self.assertNotIn("TOP_SECRET_TOKEN", public)
            self.assertNotIn("OPENROUTER_SECRET", public)
            self.assertNotIn("weight_grams // 1000", public)
            self.assertNotIn(runner.BASE_PROMPT, public)
            self.assertNotIn(runner.UNIT_COMMAND, public)
            self.assertNotIn(runner.REQUIRED_COMMAND, public)
            self.assertFalse((output / "receipt.json").stat().st_mode & stat.S_IROTH)
            self.assertTrue((output / "arm-a-final.py").is_file())
            self.assertTrue((output / "arm-b-final.py").is_file())
            self.assertTrue((output / "arm-map.json").is_file())
            self.assertNotIn("treatment", public)

    def test_openrouter_opt_in_is_same_for_both_arms(self):
        with tempfile.TemporaryDirectory() as temp:
            auth_home, output, calls, popen = self.setup_run(temp)
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(auth_home)}, clear=False), \
                 mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "OPENROUTER_SECRET"}), \
                 mock.patch.object(runner.subprocess, "Popen", side_effect=popen), \
                 mock.patch.object(
                     runner.core, "_collect_events",
                     side_effect=lambda process, **kwargs: fake_events(process.prompt),
                 ):
                runner.run_pair(
                    codex="/fake/codex", model="gpt-5.6", reasoning_effort="high",
                    seed=3, output_dir=output, allow_openrouter_key=True,
                )
            self.assertEqual(
                [call["env"].get("OPENROUTER_API_KEY") for call in calls],
                ["OPENROUTER_SECRET", "OPENROUTER_SECRET"],
            )

    def test_missing_required_matching_completion_fails_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            auth_home, output, calls, popen = self.setup_run(temp)
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(auth_home)}, clear=False), \
                 mock.patch.object(runner.subprocess, "Popen", side_effect=popen), \
                 mock.patch.object(
                     runner.core, "_collect_events",
                     side_effect=lambda process, **kwargs: fake_events(process.prompt, required_id_match=False),
                 ):
                receipt = runner.run_pair(
                    codex="/fake/codex", model="gpt-5.6", reasoning_effort="high",
                    seed=2, output_dir=output,
                )
            self.assertEqual(receipt["status"], "failed")
            self.assertTrue(all(arm["required_suite_invocation_observed"] for arm in receipt["arms"].values()))
            self.assertTrue(all(arm["required_suite_exit"] is None for arm in receipt["arms"].values()))

    def test_unknown_choice_ids_are_unscored_and_not_echoed(self):
        bad_choice = json.dumps({
            "status": "remote-choice",
            "ordered": [{"id": "unexpected", "kind": "unit", "command": runner.UNIT_COMMAND}],
            "required": [{"id": "full", "command": runner.REQUIRED_COMMAND}],
            "executed": False,
        })
        with tempfile.TemporaryDirectory() as temp:
            auth_home, output, calls, popen = self.setup_run(temp)
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(auth_home)}, clear=False), \
                 mock.patch.object(runner.subprocess, "Popen", side_effect=popen), \
                 mock.patch.object(
                     runner.core, "_collect_events",
                     side_effect=lambda process, **kwargs: fake_events(
                         process.prompt, choice_payload=bad_choice,
                     ),
                 ):
                receipt = runner.run_pair(
                    codex="/fake/codex", model="gpt-5.6", reasoning_effort="high",
                    seed=1, output_dir=output,
                )
            treatment = next(arm for arm in receipt["arms"].values()
                             if arm["choice"]["status"] == "unscored")
            self.assertEqual(treatment["choice"], {"status": "unscored", "candidate_ids": []})
            self.assertEqual(treatment["rank_output_status"], "invalid_choice")
            self.assertEqual(treatment["rank_exit_code"], 0)
            self.assertNotIn("unexpected", (output / "receipt.json").read_text(encoding="utf-8"))

    def test_completion_without_command_matches_started_id_and_validates_failure(self):
        base = time.monotonic()
        rank = "python -m jevcompass tests rank --input test-options.json --json"
        choice = json.dumps({
            "status": "remote-choice", "ordered": [
                {"id": "unit", "kind": "unit", "command": runner.UNIT_COMMAND},
                {"id": "contract", "kind": "contract", "command": runner.CONTRACT_COMMAND}],
            "required": [{"id": "full", "command": runner.REQUIRED_COMMAND}],
            "executed": False,
        })
        lines = [
            command_event("item.started", "rank", rank),
            json.dumps({"type": "item.completed", "item": {
                "id": "rank", "type": "command_execution", "exit_code": 0,
                "aggregated_output": choice}}),
            command_event("item.started", "test", runner.UNIT_COMMAND),
            json.dumps({"type": "item.completed", "item": {
                "id": "test", "type": "command_execution", "exit_code": 1,
                "aggregated_output": "FAIL: test_partial_kilogram_rounds_up (tests.test_unit_quote.QuoteShippingUnitTests)\nPRIVATE SOURCE"}}),
        ]
        result = runner._event_receipts(lines, [base + n / 10 for n in range(4)], base)
        self.assertEqual(result["choice"], {"status": "remote-choice", "candidate_ids": ["unit", "contract"]})
        self.assertEqual(result["focused_test_exits"], [{"candidate_id": "unit", "exit_code": 1}])
        self.assertEqual(result["first_useful_error_ms"], 300.0)
        self.assertNotIn("PRIVATE SOURCE", json.dumps(result))
        unrelated = lines[:3] + [lines[3].replace("test_partial_kilogram_rounds_up", "test_unrelated")]
        self.assertIsNone(runner._event_receipts(unrelated, [base + n / 10 for n in range(4)], base)["first_useful_error_ms"])

    def test_rerun_uses_final_observed_test_exits_without_erasing_first_failure(self):
        base = time.monotonic()
        sequence = []
        for identifier, command, code, output in (
            ("u1", runner.UNIT_COMMAND, 1, "FAIL: test_partial_kilogram_rounds_up (case)"),
            ("u2", runner.UNIT_COMMAND, 0, "OK"),
            ("r1", runner.REQUIRED_COMMAND, 1, "FAILED"),
            ("r2", runner.REQUIRED_COMMAND, 0, "OK"),
        ):
            sequence.append(command_event("item.started", identifier, command))
            sequence.append(command_event("item.completed", identifier, command,
                                          exit_code=code, aggregated_output=output))
        receipt = runner._event_receipts(sequence, [base + n / 100 for n in range(len(sequence))], base)
        self.assertEqual(receipt["focused_test_exits"], [{"candidate_id": "unit", "exit_code": 0}])
        self.assertEqual(receipt["required_suite_exit"], 0)
        self.assertEqual(receipt["first_useful_error_ms"], 10.0)


if __name__ == "__main__":
    unittest.main()
