"""End-to-end tests for the supervisor timeout-observation bridge."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT = ROOT / "scripts" / "pilot_ambiguous_timeout_pair.py"
SPEC = importlib.util.spec_from_file_location("pilot_ambiguous_timeout_pair_observations", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load timeout pair runner")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

from jevcompass.triage import TimeoutObservation


def focus_events():
    command = list(shlex.split(runner.FOCUSED_COMMAND))
    return [
        {"type": "item.started", "item": {
            "id": "focus", "type": "command_execution", "command": command,
        }},
        {"type": "item.completed", "item": {
            "id": "focus", "type": "command_execution", "command": command,
            "exit_code": 1,
            "aggregated_output": (
                "ERROR: test_preloaded_item_is_available_without_waiting\\n"
                "TimeoutError: inbox-not-ready"
            ),
        }},
    ]


def serialized(event):
    return json.dumps(event)


def bridge_body(observations_marker=None):
    result = {
        "observed_exit_status": 1,
        "failure_kind": "timeout",
        "hypothesis_ids": list(runner.TIMEOUT_CANDIDATES),
    }
    if observations_marker is not None:
        result["timeout_observations"] = observations_marker
    return result


class TimeoutBridgeObservationTests(unittest.TestCase):
    def test_command_builder_accepts_only_unique_consistent_production_enums(self):
        with self.assertRaises(ValueError):
            runner._triage_command(1, ["progress_observed"])
        with self.assertRaises(ValueError):
            runner._triage_command(1, [
                TimeoutObservation.PROGRESS_OBSERVED,
                TimeoutObservation.PROGRESS_OBSERVED,
            ])
        with self.assertRaises(ValueError):
            runner._triage_command(1, [
                TimeoutObservation.PROGRESS_OBSERVED,
                TimeoutObservation.NO_PROGRESS_OBSERVED,
            ])

    def test_shim_forwards_exact_allowlisted_facts_through_http_to_production_triage(self):
        facts = [
            TimeoutObservation.PROGRESS_OBSERVED,
            TimeoutObservation.WAIT_CONDITION_SATISFIABLE,
        ]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            with runner._SupervisorBridge() as bridge:
                bridge.observe_line(serialized(focus_events()[1]))
                shim_path = runner._write_python_shim(home, bridge.url)
                env = os.environ.copy()
                env.pop("OPENROUTER_API_KEY", None)
                env["PATH"] = str(shim_path) + os.pathsep + env.get("PATH", "")
                env["PYTHONPATH"] = str(ROOT / "src")
                env["JEVCOMPASS_TRIAGE_BRIDGE_URL"] = bridge.url
                command = list(runner._triage_command(1, facts))
                with mock.patch("jevcompass.triage.DecisionsClient") as client:
                    client.return_value.decide.return_value = {
                        "diagnostic": {
                            "type": "choice",
                            "choice": "timeout_contention",
                            "confidence": 0.91,
                        }
                    }
                    result = subprocess.run(
                        command, env=env, capture_output=True, text=True, timeout=8,
                    )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["status"], "remote-choice")
                state = client.return_value.decide.call_args.args[0]
                self.assertEqual(
                    state["timeout_observations"],
                    [fact.value for fact in facts],
                )
                receipt = bridge.receipt()
                self.assertEqual(
                    receipt["timeout_observations"],
                    [fact.value for fact in facts],
                )
                self.assertEqual(receipt["state"], "accepted")

    def test_legacy_shim_request_preserves_request_and_provider_state_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            with runner._SupervisorBridge() as bridge:
                bridge.observe_line(serialized(focus_events()[1]))
                shim_path = runner._write_python_shim(home, bridge.url)
                env = os.environ.copy()
                env.pop("OPENROUTER_API_KEY", None)
                env["PATH"] = str(shim_path) + os.pathsep + env.get("PATH", "")
                env["PYTHONPATH"] = str(ROOT / "src")
                env["JEVCOMPASS_TRIAGE_BRIDGE_URL"] = bridge.url
                with mock.patch("jevcompass.triage.DecisionsClient") as client:
                    client.return_value.decide.return_value = {
                        "diagnostic": {
                            "type": "choice",
                            "choice": "timeout_nonterminating",
                            "confidence": 0.91,
                        }
                    }
                    result = subprocess.run(
                        list(runner._triage_command(1)),
                        env=env, capture_output=True, text=True, timeout=8,
                    )
                self.assertEqual(result.returncode, 0, result.stderr)
                state = client.return_value.decide.call_args.args[0]
                self.assertNotIn("timeout_observations", state)
                self.assertEqual(bridge.receipt()["timeout_observations"], [])

    def test_unknown_duplicate_and_contradictory_http_facts_never_call_provider(self):
        invalid_values = (
            ["PRIVATE_SENTINEL"],
            ["progress_observed", "progress_observed"],
            ["progress_observed", "no_progress_observed"],
        )
        for values in invalid_values:
            with self.subTest(values=values):
                with runner._SupervisorBridge() as bridge:
                    bridge.observe_line(serialized(focus_events()[1]))
                    with mock.patch("jevcompass.triage.DecisionsClient") as client:
                        status, response = runner_test_bridge_post(
                            bridge.url, bridge_body(values),
                        )
                    self.assertEqual(status, 400)
                    self.assertEqual(response, b'{"status":"unavailable"}')
                    self.assertEqual(client.call_count, 0)
                    receipt_text = json.dumps(bridge.receipt())
                    self.assertNotIn("PRIVATE_SENTINEL", receipt_text)
                    self.assertEqual(bridge.receipt()["timeout_observations"], [])

    def test_shim_does_not_forward_invalid_or_contradictory_values(self):
        invalid_sequences = (
            ["PRIVATE_SENTINEL"],
            ["progress_observed", "no_progress_observed"],
        )
        for values in invalid_sequences:
            with self.subTest(values=values), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / "home"
                with runner._SupervisorBridge() as bridge:
                    bridge.observe_line(serialized(focus_events()[1]))
                    shim_path = runner._write_python_shim(home, bridge.url)
                    env = os.environ.copy()
                    env.pop("OPENROUTER_API_KEY", None)
                    env["PATH"] = str(shim_path) + os.pathsep + env.get("PATH", "")
                    env["PYTHONPATH"] = str(ROOT / "src")
                    env["JEVCOMPASS_TRIAGE_BRIDGE_URL"] = bridge.url
                    command = list(runner._triage_command(1))
                    for value in values:
                        command.extend(("--timeout-observation", value))
                    with mock.patch("jevcompass.triage.DecisionsClient") as client:
                        result = subprocess.run(
                            command, env=env, capture_output=True, text=True, timeout=8,
                        )
                    if "PRIVATE_SENTINEL" in values:
                        self.assertNotEqual(result.returncode, 0)
                    else:
                        self.assertEqual(result.returncode, 0)
                        self.assertEqual(json.loads(result.stdout)["status"], "no-remote-choice")
                    self.assertEqual(bridge.receipt()["request_count"], 0)
                    self.assertEqual(client.call_count, 0)
                    safe_receipt = json.dumps(bridge.receipt())
                    self.assertNotIn("PRIVATE_SENTINEL", safe_receipt)

    def test_event_gate_accepts_facts_after_completed_safe_local_inspection(self):
        focus = focus_events()
        inspection = [
            {"type": "item.started", "item": {
                "id": "inspect", "type": "command_execution",
                "command": ["sed", "-n", "1,20p", "src/inbox.py"],
            }},
            {"type": "item.completed", "item": {
                "id": "inspect", "type": "command_execution",
                "exit_code": 0,
            }},
        ]
        command = list(runner._triage_command(
            1, [TimeoutObservation.PROGRESS_OBSERVED],
        ))
        triage = [
            {"type": "item.started", "item": {
                "id": "triage", "type": "command_execution", "command": command,
            }},
            {"type": "item.completed", "item": {
                "id": "triage", "type": "command_execution", "exit_code": 0,
                "aggregated_output": json.dumps({
                    "status": "no-remote-choice",
                    "steps": [
                        {"id": item, "title": "fixed", "instruction": "fixed"}
                        for item in runner.TIMEOUT_CANDIDATES
                    ],
                    "observed_exit_status": 1,
                    "test_failed": True,
                    "executed": False,
                }),
            }},
        ]
        rows = [serialized(item) for item in focus + inspection + triage]
        result = runner._event_receipts(rows, [float(i) for i in range(len(rows))], 0.0)
        self.assertTrue(result["triage_phase_proven"])
        self.assertFalse(result["triage_invalid_invocation_observed"])
        self.assertEqual(result["triage_status"], "valid_local_abstention")

    def test_unsafe_or_incomplete_inspection_still_blocks_phase_credit(self):
        for command in (
            ["cat", "src/inbox.py", "&&", "touch", "sentinel"],
            ["sed", "-i", "s/old/new/", "src/inbox.py"],
            ["cat", "src/inbox.py"],
        ):
            with self.subTest(command=command):
                focus = focus_events()
                inspection = [{"type": "item.started", "item": {
                    "id": "inspect", "type": "command_execution", "command": command,
                }}]
                triage_command = list(runner._triage_command(
                    1, [TimeoutObservation.PROGRESS_OBSERVED],
                ))
                triage = {"type": "item.started", "item": {
                    "id": "triage", "type": "command_execution",
                    "command": triage_command,
                }}
                rows = [serialized(item) for item in focus + inspection + [triage]]
                result = runner._event_receipts(rows, [float(i) for i in range(len(rows))], 0.0)
                self.assertFalse(result["triage_phase_proven"])
                self.assertTrue(result["triage_invalid_invocation_observed"])


def runner_test_bridge_post(url, value):
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    request = Request(
        url, data=json.dumps(value, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=2.0) as response:
            return response.status, response.read()
    except HTTPError as error:
        try:
            return error.code, error.read()
        finally:
            error.close()


if __name__ == "__main__":
    unittest.main()
