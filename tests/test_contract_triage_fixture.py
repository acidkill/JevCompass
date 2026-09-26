"""Guard the fictional ambiguous contract triage fixture."""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import subprocess
import sys
import unittest


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ambiguous_contract_triage"
FOCUSED = [
    sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests",
    "-p", "test_invoice.py", "-v",
]
FULL = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]


def run_fixture(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=FIXTURE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
        check=False,
    )


class ContractTriageFixtureTests(unittest.TestCase):
    def test_seeded_focused_and_full_validation_fail_identifiably(self):
        focused = run_fixture(FOCUSED)
        self.assertEqual(focused.returncode, 1, focused.stdout)
        self.assertIn("FAIL: test_invoice_total_matches_legacy_golden", focused.stdout)
        self.assertIn("Decimal('0.02') != Decimal('0.01')", focused.stdout)
        self.assertIn("Ran 2 tests", focused.stdout)

        full = run_fixture(FULL)
        self.assertEqual(full.returncode, 1, full.stdout)
        self.assertIn("FAIL: test_invoice_total_matches_legacy_golden", full.stdout)
        self.assertIn("Decimal('0.02') != Decimal('0.01')", full.stdout)
        self.assertIn("Ran 2 tests", full.stdout)

    def test_local_evidence_demonstrates_both_policy_outcomes(self):
        golden = json.loads((FIXTURE / "legacy_golden.json").read_text(encoding="utf-8"))
        amounts = [Decimal(value) for value in golden["line_amounts"]]
        expected_legacy_total = Decimal(golden["expected_total"])
        line_then_sum = sum(
            (amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for amount in amounts),
            Decimal("0.00"),
        )
        sum_then_round = sum(amounts, Decimal("0.00")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        self.assertEqual(line_then_sum, Decimal("0.02"))
        self.assertEqual(sum_then_round, Decimal("0.01"))
        self.assertEqual(expected_legacy_total, sum_then_round)

        contract = (FIXTURE / "INVOICE_CONTRACT.md").read_text(encoding="utf-8").lower()
        self.assertIn("cent", contract)
        self.assertIn("half-up", contract)
        self.assertNotIn("line", contract)
        self.assertNotIn("subtotal", contract)
        self.assertNotIn("invoice total", contract)

    def test_candidate_fixture_does_not_contain_evaluator_artifacts(self):
        candidate_files = [
            path for path in FIXTURE.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
            and path.suffix in {".md", ".py", ".json"}
        ]
        self.assertTrue(candidate_files)
        for path in candidate_files:
            relative = path.relative_to(FIXTURE)
            self.assertNotIn("evaluation", relative.parts)
            self.assertNotIn("expected.md", path.name.lower())
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("blind scoring", text.lower())
            self.assertNotIn("hard fail", text.lower())
            self.assertNotIn("score rubric", text.lower())

        prompt = (FIXTURE / "README.md").read_text(encoding="utf-8").lower()
        self.assertIn("do not invent requirements", prompt)
        self.assertIn("report what remains unresolved", prompt)
        for answer_leak in ("per-line", "sum-then-round", "rounding scope", "0.02", "0.01"):
            self.assertNotIn(answer_leak, prompt)


if __name__ == "__main__":
    unittest.main()
