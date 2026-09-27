"""Offline tests for profile-driven supervisor triage transport."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from jevcompass.decisions import DecisionsClient
from pilot_profile_triage_bridge import (
    ProfileTriageBridge, ProfileTriageSpec, command_for, write_python_shim,
)


TIMEOUT_SPEC = ProfileTriageSpec(
    failure_kinds=("timeout",),
    hypotheses=("timeout_contention", "timeout_nonterminating"),
    allowed_observations={"timeout": (
        "progress_observed", "no_progress_observed", "resource_contention_observed",
        "no_resource_contention_observed", "wait_condition_satisfiable",
        "wait_condition_unsatisfiable",
    )},
    rank_hypotheses=True,
)


def reply_transport(answers=None, usage=None, *, error=None, seen=None):
    def transport(url, body, api_key, timeout):
        if seen is not None:
            seen.append({"url": url, "body": json.loads(body), "key": api_key})
        if error is not None:
            raise error
        return json.dumps({"answers": answers or {}, "usage": usage}).encode()
    return transport


def successful_answers():
    return {
        "diagnostic": {
            "type": "choice", "choice": "timeout_nonterminating", "confidence": 0.91,
        },
        "hypothesis_pair_0_1": {
            "type": "choice", "choice": "timeout_nonterminating", "confidence": 0.88,
        },
    }


def post(url, payload):
    data = json.dumps(payload, separators=(",", ":")).encode()
    request = Request(url, data=data, headers={"Content-Type": "application/json"},
                      method="POST")
    try:
        with urlopen(request, timeout=2) as response:
            return response.status, response.read()
    except HTTPError as error:
        try:
            return error.code, error.read()
        finally:
            error.close()


def request_for(**overrides):
    value = {
        "observed_exit_status": 1,
        "failure_kinds": ["timeout"],
        "hypotheses": ["timeout_contention", "timeout_nonterminating"],
        "observations": {"timeout": []},
        "rank_hypotheses": True,
    }
    value.update(overrides)
    return value


class ProfileTriageBridgeTests(unittest.TestCase):
    def private_dir(self, temporary):
        directory = Path(temporary) / "private"
        directory.mkdir(mode=0o700)
        return directory

    def test_exact_shim_keeps_key_parent_only_and_persists_choice_rank_and_usage(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = self.private_dir(temporary)
            seen = []
            factory = lambda: DecisionsClient(
                api_key="SUPERVISOR_ONLY_SECRET", model="synthetic",
                transport=reply_transport(
                    successful_answers(),
                    {"input_tokens": 14, "output_tokens": 5, "cost": 0.0012},
                    seen=seen,
                ),
            )
            receipt_path = private / "decision.json"
            bridge = ProfileTriageBridge(
                TIMEOUT_SPEC, receipt_path=receipt_path, client_factory=factory,
            )
            with bridge:
                self.assertTrue(bridge.observe_focused_failure(1, failure_confirmed=True))
                bin_dir = write_python_shim(private, bridge)
                child_env = {
                    "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                    "HOME": str(private), "PYTHONPATH": str(ROOT / "src"),
                    "JEVCOMPASS_TRIAGE_BRIDGE_URL": bridge.url,
                }
                self.assertNotIn("OPENROUTER_API_KEY", child_env)
                command = command_for(TIMEOUT_SPEC, 1)
                child = subprocess.run(
                    [str(bin_dir / "python"), *command[1:]],
                    cwd=ROOT, env=child_env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    timeout=4, check=False,
                )
                self.assertEqual(child.returncode, 0, child.stderr)
                response = json.loads(child.stdout)
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(receipt["diagnostic_choice_id"], "timeout_nonterminating")
            self.assertEqual(receipt["diagnostic_selection_source"], "remote_preferred_next_step")
            self.assertEqual(receipt["hypothesis_order"], [
                "timeout_nonterminating", "timeout_contention",
            ])
            self.assertEqual(receipt["hypothesis_ranking_status"], "complete")
            self.assertEqual(receipt["provider_transport_call_count"], 1)
            self.assertEqual(receipt["decision_usage"], {
                "input_tokens": 14, "output_tokens": 5, "cost_usd": 0.0012,
            })
            self.assertFalse(receipt["cache_hit"])
            self.assertEqual(response["hypothesis_order"], receipt["hypothesis_order"])
            self.assertEqual(response["steps"][0]["selection_source"], "remote_preferred_next_step")
            self.assertEqual(seen[0]["key"], "SUPERVISOR_ONLY_SECRET")
            self.assertEqual(seen[0]["body"]["state"], {
                "test_outcome": "failed", "failure_kinds": ["timeout"],
                "hypotheses": ["timeout_contention", "timeout_nonterminating"],
            })
            self.assertNotIn("private/source.py", json.dumps(seen[0]["body"]))
            self.assertNotIn("SUPERVISOR_ONLY_SECRET", child.stdout + child.stderr)
            self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(bridge.receipt()["request_count"], 1)

    def test_local_resolution_is_saved_without_provider_call_or_fake_ranking(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = self.private_dir(temporary)
            calls = []
            factory = lambda: DecisionsClient(
                api_key="unused", transport=reply_transport(successful_answers(), seen=calls),
            )
            path = private / "decision.json"
            bridge = ProfileTriageBridge(TIMEOUT_SPEC, receipt_path=path, client_factory=factory)
            with bridge:
                bridge.observe_focused_failure(1, failure_confirmed=True)
                status, body = post(bridge.url, request_for(
                    observations={"timeout": ["wait_condition_unsatisfiable"]},
                ))
            self.assertEqual(status, 200)
            response = json.loads(body)
            receipt = json.loads(path.read_text())
            self.assertEqual(receipt["decision_reason"], "local_resolution")
            self.assertEqual(receipt["diagnostic_choice_id"], "timeout_nonterminating")
            self.assertEqual(receipt["hypothesis_ranking_status"], "not_established")
            self.assertEqual(receipt["hypothesis_order"], [])
            self.assertEqual(receipt["provider_transport_call_count"], 0)
            self.assertEqual(receipt["decision_usage_status"], "not_invoked")
            self.assertEqual(calls, [])
            self.assertEqual(response["status"], "no-remote-choice")

    def test_local_abstention_is_preserved_without_provider_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = self.private_dir(temporary)
            calls = []
            spec = ProfileTriageSpec(
                failure_kinds=("timeout",), hypotheses=("timeout_nonterminating",),
                allowed_observations={}, rank_hypotheses=True,
            )
            factory = lambda: DecisionsClient(
                api_key="unused", transport=reply_transport(successful_answers(), seen=calls),
            )
            path = private / "decision.json"
            bridge = ProfileTriageBridge(spec, receipt_path=path, client_factory=factory)
            with bridge:
                bridge.observe_focused_failure(1, failure_confirmed=True)
                status, body = post(bridge.url, {
                    "observed_exit_status": 1, "failure_kinds": ["timeout"],
                    "hypotheses": ["timeout_nonterminating"], "observations": {},
                    "rank_hypotheses": True,
                })
            response, receipt = json.loads(body), json.loads(path.read_text())
            self.assertEqual(status, 200)
            self.assertEqual(receipt["decision_reason"], "local_abstention")
            self.assertEqual(receipt["hypothesis_ranking_status"], "not_established")
            self.assertEqual(receipt["provider_transport_call_count"], 0)
            self.assertEqual(receipt["decision_usage_status"], "not_invoked")
            self.assertEqual(response["status"], "no-remote-choice")
            self.assertEqual(calls, [])

    def test_low_confidence_keeps_local_next_step_and_usage_without_claiming_remote_choice(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = self.private_dir(temporary)
            factory = lambda: DecisionsClient(
                api_key="synthetic",
                transport=reply_transport({
                    "diagnostic": {
                        "type": "choice", "choice": "timeout_contention", "confidence": 0.2,
                    },
                    "hypothesis_pair_0_1": {
                        "type": "choice", "choice": "timeout_contention", "confidence": 0.9,
                    },
                }, {"input_tokens": 9, "output_tokens": 3, "cost": None}),
            )
            spec = ProfileTriageSpec(
                TIMEOUT_SPEC.failure_kinds, TIMEOUT_SPEC.hypotheses, {}, True,
            )
            path = private / "decision.json"
            bridge = ProfileTriageBridge(spec, receipt_path=path, client_factory=factory)
            with bridge:
                bridge.observe_focused_failure(1, failure_confirmed=True)
                status, body = post(bridge.url, request_for(
                    observations={}, rank_hypotheses=True,
                ))
            self.assertEqual(status, 200)
            response, receipt = json.loads(body), json.loads(path.read_text())
            self.assertEqual(response["status"], "no-remote-choice")
            self.assertEqual(receipt["decision_reason"], "insufficient_confidence")
            self.assertEqual(receipt["diagnostic_selection_source"], "unranked_local_fallback")
            self.assertEqual(receipt["hypothesis_ranking_status"], "complete")
            self.assertEqual(receipt["provider_transport_call_count"], 1)
            self.assertEqual(receipt["decision_usage_status"], "reported")

    def test_accepted_diagnostic_choice_survives_incomplete_causal_ranking(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = self.private_dir(temporary)
            factory = lambda: DecisionsClient(
                api_key="synthetic",
                transport=reply_transport({
                    "diagnostic": {
                        "type": "choice", "choice": "timeout_nonterminating", "confidence": 0.9,
                    },
                }, {"input_tokens": 6, "output_tokens": 2, "cost": None}),
            )
            path = private / "decision.json"
            bridge = ProfileTriageBridge(TIMEOUT_SPEC, receipt_path=path,
                                         client_factory=factory)
            with bridge:
                bridge.observe_focused_failure(1, failure_confirmed=True)
                status, body = post(bridge.url, request_for())
            response, receipt = json.loads(body), json.loads(path.read_text())
            self.assertEqual(status, 200)
            self.assertEqual(response["status"], "remote-choice")
            self.assertEqual(receipt["diagnostic_choice_id"], "timeout_nonterminating")
            self.assertEqual(receipt["hypothesis_ranking_status"], "incomplete")
            self.assertEqual(receipt["hypothesis_order"], [])
            self.assertEqual(receipt["provider_transport_call_count"], 1)
            self.assertEqual(receipt["decision_usage_status"], "reported")

    def test_provider_error_is_generic_fallback_and_counted_at_transport_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = self.private_dir(temporary)
            path = private / "decision.json"
            factory = lambda: DecisionsClient(
                api_key="synthetic",
                transport=reply_transport(error=RuntimeError("PRIVATE_PROVIDER_DETAIL")),
            )
            bridge = ProfileTriageBridge(TIMEOUT_SPEC, receipt_path=path, client_factory=factory)
            with bridge:
                bridge.observe_focused_failure(1, failure_confirmed=True)
                status, body = post(bridge.url, request_for())
            receipt = json.loads(path.read_text())
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["decision_reason"], "provider_error")
            self.assertEqual(receipt["provider_transport_call_count"], 1)
            self.assertNotIn("PRIVATE_PROVIDER_DETAIL", body.decode())
            self.assertNotIn("PRIVATE_PROVIDER_DETAIL", path.read_text())

    def test_malformed_decision_is_fallback_and_retains_valid_usage(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = self.private_dir(temporary)
            path = private / "decision.json"
            factory = lambda: DecisionsClient(
                api_key="synthetic",
                transport=reply_transport(
                    {"wrong-question": {}},
                    {"input_tokens": 8, "output_tokens": 2, "cost": 0.0004},
                ),
            )
            bridge = ProfileTriageBridge(TIMEOUT_SPEC, receipt_path=path, client_factory=factory)
            with bridge:
                bridge.observe_focused_failure(1, failure_confirmed=True)
                status, body = post(bridge.url, request_for())
            receipt = json.loads(path.read_text())
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["decision_reason"], "invalid_response")
            self.assertEqual(receipt["provider_transport_call_count"], 1)
            self.assertEqual(receipt["decision_usage"]["input_tokens"], 8)
            self.assertEqual(receipt["decision_usage_status"], "reported")

    def test_premature_duplicate_and_invalid_enum_requests_never_call_provider(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = self.private_dir(temporary)
            calls = []
            factory = lambda: DecisionsClient(
                api_key="synthetic", transport=reply_transport(successful_answers(), seen=calls),
            )
            early = ProfileTriageBridge(
                TIMEOUT_SPEC, receipt_path=private / "early.json", client_factory=factory,
            )
            with early:
                status, _ = post(early.url, request_for())
            self.assertEqual(status, 409)
            self.assertEqual(json.loads((private / "early.json").read_text())["request_state"],
                             "premature")
            self.assertEqual(calls, [])

            invalid = ProfileTriageBridge(
                TIMEOUT_SPEC, receipt_path=private / "invalid.json", client_factory=factory,
            )
            with invalid:
                invalid.observe_focused_failure(1, failure_confirmed=True)
                status, _ = post(invalid.url, request_for(
                    hypotheses=["timeout_contention", "PRIVATE_TEXT"],
                ))
                self.assertEqual(status, 400)
                status, _ = post(invalid.url, request_for())
            self.assertEqual(status, 429)
            self.assertEqual(calls, [])
            self.assertEqual(json.loads((private / "invalid.json").read_text())["status"],
                             "rejected")

    def test_spec_and_command_reject_non_enum_and_zero_exit(self):
        with self.assertRaises(ValueError):
            ProfileTriageSpec(("timeout",), ("raw failure text",), {})
        with self.assertRaises(ValueError):
            command_for(TIMEOUT_SPEC, 0)
        with self.assertRaises(ValueError):
            command_for(TIMEOUT_SPEC, 1, {"timeout": ["raw diagnostic"]})


if __name__ == "__main__":
    unittest.main()
