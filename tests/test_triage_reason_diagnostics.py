"""Offline tests for allowlisted triage outcome diagnostics."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest
from urllib.request import Request, urlopen
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from jevcompass.decisions import DecisionResponse, DecisionUsage, DecisionsClient, DecisionsError
from jevcompass.triage import (
    FailureKind, HypothesisId, TriageDecisionReason, TimeoutObservation, triage_failure,
)

SCRIPT = ROOT / "scripts" / "pilot_ambiguous_timeout_pair.py"
SPEC = importlib.util.spec_from_file_location("pilot_ambiguous_timeout_pair_reasons", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load timeout pair runner")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

CANDIDATES = tuple(HypothesisId(item) for item in runner.TIMEOUT_CANDIDATES)
USAGE = DecisionUsage(input_tokens=11, output_tokens=4, cost_usd=None)


class AnswersClient:
    def __init__(self, answers=None, error=None):
        self.answers = answers
        self.error = error

    def decide(self, _state, _questions):
        if self.error is not None:
            raise self.error
        return self.answers


class UsageClient(DecisionsClient):
    def __init__(self, response):
        self.response = response

    def decide_with_usage(self, _state, _questions):
        return self.response


def run(client):
    return triage_failure((FailureKind.TIMEOUT,), CANDIDATES, 1, client=client)


class TriageReasonDiagnosticTests(unittest.TestCase):
    def test_local_resolution_is_explicit_and_skips_client(self):
        class NeverCall:
            def decide(self, *_args):
                raise AssertionError("local resolution must not call client")

        result = triage_failure(
            (FailureKind.TIMEOUT,), CANDIDATES, 1, client=NeverCall(),
            timeout_observations=(TimeoutObservation.WAIT_CONDITION_UNSATISFIABLE,),
        )
        self.assertEqual(result.decision_reason, TriageDecisionReason.LOCAL_RESOLUTION)

    def test_missing_candidates_are_local_abstention_not_resolution(self):
        result = triage_failure((FailureKind.TIMEOUT,), (), 1)
        self.assertEqual(result.status, "no-remote-choice")
        self.assertEqual(result.steps, ())
        self.assertEqual(result.decision_reason, TriageDecisionReason.LOCAL_ABSTENTION)

    def test_accepted_choice_has_fixed_reason(self):
        result = run(AnswersClient({
            "diagnostic": {
                "type": "choice", "choice": CANDIDATES[0].value, "confidence": 0.9,
            },
        }))
        self.assertEqual(result.status, "remote-choice")
        self.assertEqual(result.decision_reason, TriageDecisionReason.ACCEPTED)

    def test_malformed_response_preserves_valid_usage(self):
        result = run(UsageClient(DecisionResponse({"other": {}}, USAGE)))
        self.assertEqual(result.decision_reason, TriageDecisionReason.INVALID_RESPONSE)
        self.assertEqual(result.decision_usage, USAGE)

    def test_low_numeric_confidence_is_distinct_and_preserves_usage(self):
        result = run(UsageClient(DecisionResponse({
            "diagnostic": {
                "type": "choice", "choice": CANDIDATES[0].value, "confidence": 0.4,
            },
        }, USAGE)))
        self.assertEqual(
            result.decision_reason, TriageDecisionReason.INSUFFICIENT_CONFIDENCE,
        )
        self.assertEqual(result.decision_usage, USAGE)

    def test_malformed_confidence_is_invalid_not_low_confidence(self):
        result = run(AnswersClient({
            "diagnostic": {
                "type": "choice", "choice": CANDIDATES[0].value, "confidence": "low",
            },
        }))
        self.assertEqual(result.decision_reason, TriageDecisionReason.INVALID_RESPONSE)

    def test_negative_confidence_is_invalid_response(self):
        result = run(AnswersClient({
            "diagnostic": {
                "type": "choice", "choice": CANDIDATES[0].value, "confidence": -0.1,
            },
        }))
        self.assertEqual(result.decision_reason, TriageDecisionReason.INVALID_RESPONSE)

    def test_extreme_integer_confidence_is_invalid_response(self):
        result = run(AnswersClient({
            "diagnostic": {
                "type": "choice", "choice": CANDIDATES[0].value, "confidence": 10**10000,
            },
        }))
        self.assertEqual(result.decision_reason, TriageDecisionReason.INVALID_RESPONSE)

    def test_unwrapped_timeout_is_safe_allowlisted_reason(self):
        result = run(AnswersClient(error=TimeoutError("private timeout detail")))
        self.assertEqual(result.decision_reason, TriageDecisionReason.PROVIDER_TIMEOUT)
        self.assertNotIn("private timeout detail", repr(result))

    def test_client_error_is_generic_and_does_not_leak_error_text(self):
        result = run(AnswersClient(error=DecisionsError("unavailable: private detail")))
        self.assertEqual(result.decision_reason, TriageDecisionReason.PROVIDER_ERROR)
        self.assertNotIn("private detail", repr(result))

    def test_bridge_keeps_reason_out_of_agent_response_and_in_safe_receipt(self):
        observed = runner._local_timeout_payload(1)
        observed["decision_reason"] = "provider_error"
        request = {
            "observed_exit_status": 1, "failure_kind": "timeout",
            "hypothesis_ids": list(runner.TIMEOUT_CANDIDATES),
        }
        with runner._SupervisorBridge() as bridge:
            import shlex
            bridge.observe_line(json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "focus-completed", "type": "command_execution",
                    "command": list(shlex.split(runner.FOCUSED_COMMAND)),
                    "exit_code": 1,
                    "aggregated_output": (
                        "ERROR: test_preloaded_item_is_available_without_waiting\\n"
                        "TimeoutError: inbox-not-ready"
                    ),
                },
            }))
            with mock.patch.object(runner, "_production_timeout_payload", return_value=observed):
                body = json.dumps(request, separators=(",", ":")).encode()
                with urlopen(Request(
                    bridge.url, data=body,
                    headers={"Content-Type": "application/json"}, method="POST",
                ), timeout=2.0) as response:
                    code, raw = response.status, response.read()
            self.assertEqual(code, 200)
            response = json.loads(raw)
            self.assertNotIn("decision_reason", response)
            receipt = bridge.receipt()
            self.assertEqual(receipt["decision_reason"], "provider_error")
            self.assertNotIn("private", repr(receipt))

    def test_unattempted_bridge_reason_is_null(self):
        bridge = runner._SupervisorBridge()
        try:
            self.assertIsNone(bridge.receipt()["decision_reason"])
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main()
