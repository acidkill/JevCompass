"""Contract and legacy-golden checks for the synthetic invoice calculator."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
import unittest

from contractquote.totals import invoice_total, rounded_line_amounts


ROOT = Path(__file__).resolve().parents[1]


class InvoiceTotalTests(unittest.TestCase):
    def test_invoice_total_matches_legacy_golden(self):
        golden = json.loads((ROOT / "legacy_golden.json").read_text(encoding="utf-8"))
        amounts = [Decimal(value) for value in golden["line_amounts"]]
        expected = Decimal(golden["expected_total"])

        self.assertEqual(invoice_total(amounts), expected)

    def test_displayed_line_amounts_have_cent_precision(self):
        amounts = [Decimal("0.005"), Decimal("0.005")]

        self.assertEqual(
            rounded_line_amounts(amounts),
            [Decimal("0.01"), Decimal("0.01")],
        )


if __name__ == "__main__":
    unittest.main()
