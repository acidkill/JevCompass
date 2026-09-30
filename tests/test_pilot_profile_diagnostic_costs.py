"""Offline tests for diagnostic-cost wiring in profile triage trials."""
from __future__ import annotations

import hashlib
import importlib.util
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
from jevcompass.triage import DiagnosticCost, HypothesisId
from pilot_profile_triage_bridge import (
    ProfileTriageBridge, ProfileTriageSpec, command_for, write_python_shim,
)

SCRIPT = ROOT / "scripts" / "pilot_contract_triage_pair.py"
MODULE_SPEC = importlib.util.spec_from_file_location(
    "pilot_contract_triage_pair_diagnostic_cost_tests", SCRIPT,
)
runner = importlib.util.module_from_spec(MODULE_SPEC)
assert MODULE_SPEC and MODULE_SPEC.loader
sys.modules[MODULE_SPEC.name] = runner
MODULE_SPEC.loader.exec_module(runner)

COSTS = {
    "assertion_behavior_regression": "low",
    "assertion_expectation_drift": "high",
}


def reply_transport(answers=None, usage=None, *, seen=None):
    def transport(url, body, api_key, timeout):
        if seen is not None:
            seen.append(json.loads(body))
        return json.dumps({"answers": answers or {}, "usage": usage}).encode()
    return transport


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


class ProfileDiagnosticCostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".pilot-cost-test-", dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = self.root / "fixture"
        (self.fixture / "src").mkdir(parents=True)
        (self.fixture / "tests").mkdir()
        (self.fixture / "src" / "service.py").write_text("VALUE = 0\n", encoding="utf-8")
        (self.fixture / "tests" / "test_service.py").write_text(
            "def test_value():\n    assert True\n", encoding="utf-8"
        )
        (self.fixture / "TASK.md").write_text(
            "Repair service.py so the frozen expected value is returned.\n", encoding="utf-8"
        )
        (self.fixture / "contract.md").write_text(
            "The expected value is one.\n", encoding="utf-8"
        )
        self.oracle = self.root / "supervisor_oracle.py"
        self.oracle.write_text(
            "import argparse\nfrom pathlib import Path\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--fixture-dir', required=True)\n"
            "root = Path(p.parse_args().fixture_dir)\n"
            "raise SystemExit(0 if (root / 'src/service.py').read_text() == 'VALUE = 1\\n' else 9)\n",
            encoding="utf-8",
        )
        self.profile_data = {
            "schema_version": 1,
            "case_id": "synthetic-costs",
            "fixture_source": self.fixture.relative_to(ROOT).as_posix(),
            "task_prompt_file": "TASK.md",
            "source_file": "src/service.py",
            "focused_test_file": "tests/test_service.py",
            "focused_command": ["python", "-m", "unittest", "tests.test_service", "-q"],
            "evidence_files": {"contract": "contract.md"},
            "evidence_markers": {"contract": ["expected value", "one"]},
            "failure_markers": ["AssertionError"],
            "triage": {
                "kinds": ["assertion"],
                "hypotheses": [
                    "assertion_behavior_regression",
                    "assertion_expectation_drift",
                ],
                "accepted_ids": ["assertion_behavior_regression"],
                "accepted_statuses": ["no-remote-choice"],
                "observations": {"assertion": ["contract_underspecified"]},
                "rank_hypotheses": False,
                "diagnostic_costs": dict(COSTS),
            },
            "outcome_mode": "repair",
            "oracle_script": self.oracle.relative_to(ROOT).as_posix(),
            "oracle_sha256": hashlib.sha256(self.oracle.read_bytes()).hexdigest(),
        }
        self.profile_path = self.root / "profile.json"
        self.write_profile()

    def write_profile(self, value=None):
        self.profile_path.write_text(
            json.dumps(self.profile_data if value is None else value),
            encoding="utf-8",
        )

    def load(self):
        return runner.load_case_profile(self.profile_path)

    def test_loader_parses_valid_costs_as_enums(self):
        self.assertEqual(self.load().triage_diagnostic_costs, {
            HypothesisId("assertion_behavior_regression"): DiagnosticCost("low"),
            HypothesisId("assertion_expectation_drift"): DiagnosticCost("high"),
        })

    def test_loader_defaults_missing_costs_to_empty(self):
        del self.profile_data["triage"]["diagnostic_costs"]
        self.write_profile()
        self.assertEqual(self.load().triage_diagnostic_costs, {})

    def test_loader_rejects_invalid_costs(self):
        invalid = {
            "unknown-hypothesis": {"not_a_real_hypothesis": "low"},
            "unsupplied-hypothesis": {"confirm_behavior_contract": "low"},
            "bad-level": {"assertion_behavior_regression": "extreme"},
            "non-string-level": {"assertion_behavior_regression": 3},
            "non-object": ["assertion_behavior_regression=low"],
            "too-many-entries": dict(COSTS, confirm_behavior_contract="low"),
        }
        for name, costs in invalid.items():
            with self.subTest(name):
                self.profile_data["triage"]["diagnostic_costs"] = costs
                self.write_profile()
                with self.assertRaises(ValueError):
                    self.load()

    def test_supervisor_argv_carries_cost_flags_only_when_declared(self):
        expected = [
            "python", "-m", "jevcompass", "triage", "--exit-code", "1",
            "--kind", "assertion",
            "--hypothesis", "assertion_behavior_regression",
            "--hypothesis", "assertion_expectation_drift",
            "--diagnostic-cost", "assertion_behavior_regression=low",
            "--diagnostic-cost", "assertion_expectation_drift=high",
            "--assertion-observation", "contract_underspecified",
            "--json",
        ]
        self.assertEqual(runner._triage_argv(1, self.load()), expected)
        del self.profile_data["triage"]["diagnostic_costs"]
        self.write_profile()
        self.assertNotIn("--diagnostic-cost", runner._triage_argv(1, self.load()))

    def test_prompts_share_cost_disclosure_and_limit_its_use(self):
        base = runner._case_base_prompt(self.load())
        self.assertIn(
            "Caller-declared relative diagnostic check effort (shared with both arms): "
            "assertion_behavior_regression=low, assertion_expectation_drift=high.",
            base,
        )
        self.assertIn("not evidence of causal likelihood", base)
        treatment = runner._case_treatment_prompt(self.load(), "legacy-required-step")
        self.assertTrue(treatment.startswith(base))
        self.assertIn("may guide diagnostic next-step ordering only", treatment)
        self.assertIn("must not be used to skip required checks", treatment)

        del self.profile_data["triage"]["diagnostic_costs"]
        self.write_profile()
        self.assertNotIn("diagnostic check effort",
                         runner._case_base_prompt(self.load()))
        self.assertNotIn("diagnostic-cost",
                         runner._case_treatment_prompt(self.load(), "legacy-required-step"))

    def test_bridge_spec_and_command_match_supervisor_argv(self):
        profile = self.load()
        spec = runner._profile_bridge_spec(profile)
        self.assertEqual(spec.diagnostic_costs, COSTS)
        self.assertTrue(all(type(key) is str and type(value) is str
                            for key, value in spec.diagnostic_costs.items()))
        observed = {group: list(values)
                    for group, values in profile.triage_observations.items()}
        command = command_for(spec, 1, observed)
        self.assertEqual(command, tuple(runner._triage_argv(1, profile)))
        self.assertIn(("--diagnostic-cost", "assertion_behavior_regression=low"),
                      tuple(zip(command, command[1:])))

    def test_spec_rejects_mismatched_or_non_string_costs(self):
        valid = {
            "failure_kinds": ("assertion",),
            "hypotheses": ("assertion_behavior_regression",
                            "assertion_expectation_drift"),
            "allowed_observations": {},
        }
        self.assertEqual(ProfileTriageSpec(**valid).diagnostic_costs, {})
        with self.assertRaises(ValueError):
            ProfileTriageSpec(**valid,
                              diagnostic_costs={"assertion_behavior_regression": "extreme"})
        with self.assertRaises(ValueError):
            ProfileTriageSpec(**valid,
                              diagnostic_costs={"confirm_behavior_contract": "low"})
        with self.assertRaises(ValueError):
            ProfileTriageSpec(**valid, diagnostic_costs={
                HypothesisId("assertion_behavior_regression"): DiagnosticCost("low"),
            })
        with self.assertRaises(ValueError):
            ProfileTriageSpec(**valid,
                              diagnostic_costs=["assertion_behavior_regression=low"])

    def test_bridge_requires_matching_costs_and_forwards_them(self):
        spec = ProfileTriageSpec(
            failure_kinds=("assertion",),
            hypotheses=("assertion_behavior_regression",
                        "assertion_expectation_drift"),
            allowed_observations={},
            diagnostic_costs=dict(COSTS),
        )
        seen = []
        factory = lambda: DecisionsClient(
            api_key="synthetic",
            transport=reply_transport(seen=seen),
        )
        base_request = {
            "observed_exit_status": 1,
            "failure_kinds": ["assertion"],
            "hypotheses": ["assertion_behavior_regression",
                            "assertion_expectation_drift"],
            "observations": {},
            "rank_hypotheses": False,
            "diagnostic_costs": dict(COSTS),
        }
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary) / "private"
            private.mkdir(mode=0o700)

            def fresh_bridge(name):
                bridge = ProfileTriageBridge(
                    spec, receipt_path=private / name, client_factory=factory,
                )
                bridge.__enter__()
                self.assertTrue(bridge.observe_focused_failure(1, failure_confirmed=True))
                return bridge

            matching = fresh_bridge("matching.json")
            status, _ = post(matching.url, base_request)
            self.assertEqual(status, 200)
            matching.__exit__(None, None, None)
            self.assertEqual(len(seen), 1)
            self.assertEqual(seen[0]["state"]["diagnostic_costs"], COSTS)

            wrong = fresh_bridge("wrong.json")
            status, _ = post(wrong.url, dict(
                base_request, diagnostic_costs={
                    "assertion_behavior_regression": "medium",
                    "assertion_expectation_drift": "high",
                },
            ))
            self.assertEqual(status, 400)
            wrong.__exit__(None, None, None)

            legacy = fresh_bridge("legacy.json")
            status, _ = post(legacy.url, {
                key: value for key, value in base_request.items()
                if key != "diagnostic_costs"
            })
            self.assertEqual(status, 400)
            legacy.__exit__(None, None, None)
            self.assertEqual(len(seen), 1)

    def test_shim_intercepts_cost_command_without_fallback(self):
        spec = ProfileTriageSpec(
            failure_kinds=("assertion",),
            hypotheses=("assertion_behavior_regression",
                        "assertion_expectation_drift"),
            allowed_observations={},
            diagnostic_costs=dict(COSTS),
        )
        seen = []
        factory = lambda: DecisionsClient(
            api_key="SUPERVISOR_ONLY_SECRET",
            transport=reply_transport(seen=seen),
        )
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary) / "private"
            private.mkdir(mode=0o700)
            bridge = ProfileTriageBridge(
                spec, receipt_path=private / "decision.json", client_factory=factory,
            )
            with bridge:
                self.assertTrue(bridge.observe_focused_failure(1, failure_confirmed=True))
                bin_dir = write_python_shim(private, bridge)
                child_env = {
                    "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                    "HOME": str(private), "PYTHONPATH": str(ROOT / "src"),
                }
                command = command_for(spec, 1)
                child = subprocess.run(
                    [str(bin_dir / "python"), *command[1:]],
                    cwd=ROOT, env=child_env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    timeout=4, check=False,
                )
                self.assertEqual(child.returncode, 0, child.stderr)
                response = json.loads(child.stdout)
                self.assertEqual(response["status"], "no-remote-choice")
                self.assertEqual(bridge.receipt()["request_count"], 1)
                self.assertEqual(len(seen), 1)
                self.assertEqual(seen[0]["state"]["diagnostic_costs"], COSTS)

                altered = list(command)
                altered[altered.index("--diagnostic-cost",
                                      altered.index("--diagnostic-cost") + 1) + 1] = "medium"
                fallback_child = subprocess.run(
                    [str(bin_dir / "python"), *altered[1:]],
                    cwd=ROOT, env=child_env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    timeout=4, check=False,
                )
                # The altered command must fall back to the real interpreter
                # instead of being forwarded to the one-shot bridge.
                self.assertEqual(bridge.receipt()["request_count"], 1)
            self.assertEqual(bridge.receipt()["request_count"], 1)
            self.assertNotIn("SUPERVISOR_ONLY_SECRET",
                             child.stdout + child.stderr + fallback_child.stdout
                             + fallback_child.stderr)
            receipt = json.loads((private / "decision.json").read_text())
            self.assertEqual(receipt["provider_transport_call_count"], 1)

    def test_rank_observation_command_order_is_intercepted_not_fallback(self):
        self.profile_data["triage"]["rank_hypotheses"] = True
        self.write_profile()
        profile = self.load()
        spec = runner._profile_bridge_spec(profile)
        observed = {group: list(values)
                    for group, values in profile.triage_observations.items()}
        command = command_for(spec, 1, observed)
        self.assertEqual(command, tuple(runner._triage_argv(1, profile)))
        argv = runner._triage_argv(1, profile)
        self.assertLess(argv.index("--assertion-observation"),
                        argv.index("--rank-hypotheses"))
        self.assertLess(argv.index("--rank-hypotheses"), argv.index("--json"))

        factory = lambda: DecisionsClient(
            api_key="SUPERVISOR_ONLY_SECRET",
            transport=reply_transport(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary) / "private"
            private.mkdir(mode=0o700)
            bridge = ProfileTriageBridge(
                spec, receipt_path=private / "decision.json", client_factory=factory,
            )
            with bridge:
                self.assertTrue(bridge.observe_focused_failure(1, failure_confirmed=True))
                bin_dir = write_python_shim(private, bridge)
                child_env = {
                    "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                    "HOME": str(private), "PYTHONPATH": str(ROOT / "src"),
                }
                child = subprocess.run(
                    [str(bin_dir / "python"), *command[1:]],
                    cwd=ROOT, env=child_env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    timeout=4, check=False,
                )
                self.assertEqual(child.returncode, 0, child.stderr)
                self.assertEqual(bridge.receipt()["request_count"], 1)
                self.assertNotIn("SUPERVISOR_ONLY_SECRET",
                                 child.stdout + child.stderr)


if __name__ == "__main__":
    unittest.main()
