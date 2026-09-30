from __future__ import annotations

import importlib.util
from pathlib import Path
import shlex
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_contract_triage_pair.py"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("pilot_contract_triage_pair_commands", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class MandatoryCommandVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.profile = SimpleNamespace(
            task_prompt="Repair service.py to match the contract.",
            outcome_mode="repair",
            focused_command=(
                "python", "-m", "unittest", "discover", "-s", "tests",
                "-p", "test_service.py", "-v",
            ),
            source_file="src/service.py",
            focused_test_file="tests/test_service.py",
            evidence_files={"contract": "contract.md"},
            triage_kinds=("assertion",),
            triage_hypotheses=(
                "assertion_behavior_regression",
                "assertion_expectation_drift",
            ),
            triage_accepted_ids=(),
            rank_hypotheses=False,
            triage_observations={},
            triage_diagnostic_costs={},
        )

    def test_exact_standalone_commands_are_shared_before_treatment_extras(self):
        focused = shlex.join(self.profile.focused_command)
        full = shlex.join(shlex.split(runner.REQUIRED_COMMAND))
        diff = shlex.join(("git", "diff", "--check"))

        baseline_prompt = runner._case_base_prompt(self.profile)
        treatment_prompt = runner._case_treatment_prompt(
            self.profile, "legacy-required-step", "initial-failure",
        )

        self.assertTrue(treatment_prompt.startswith(baseline_prompt))
        for command in (focused, full, diff):
            self.assertIn(f"`{command}`", baseline_prompt)
            self.assertIn(f"`{command}`", treatment_prompt)
        self.assertIn("Before editing source, run the initial focused test", baseline_prompt)
        self.assertIn("After edits, run these commands separately in this order", baseline_prompt)
        self.assertIn("standalone command", baseline_prompt)
        self.assertIn("may request the exact enum-only triage command", treatment_prompt)
        self.assertIn("The request is optional", treatment_prompt)

    def test_legacy_and_nonrepair_prompt_behavior_is_unchanged(self):
        self.assertEqual(runner._case_base_prompt(None), runner.BASE_PROMPT)
        contract_profile = SimpleNamespace(
            task_prompt="Inspect and preserve the original failure.",
            outcome_mode="contract_triage",
            triage_diagnostic_costs={},
        )
        self.assertEqual(runner._case_base_prompt(contract_profile), contract_profile.task_prompt)


if __name__ == "__main__":
    unittest.main()
