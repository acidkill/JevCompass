"""Frozen behavioral contract tests for CartCalc."""
from decimal import Decimal
import unittest

from cartcalc.total import total_for


class TotalForContractTests(unittest.TestCase):
    def test_calculates_varied_lines_and_applies_tax_once(self):
        lines = ((Decimal("1.23"), 2), (Decimal("0.07"), 3))
        self.assertEqual(total_for(lines, Decimal("0.075")), Decimal("2.87"))

    def test_rounds_half_up_at_the_final_cent_boundary(self):
        self.assertEqual(total_for(((Decimal("0.10"), 1),), Decimal("0.05")), Decimal("0.11"))

    def test_empty_basket_is_zero_to_two_decimal_places(self):
        result = total_for((), Decimal("0"))
        self.assertEqual(result, Decimal("0.00"))
        self.assertEqual(result.as_tuple().exponent, -2)

    def test_rejects_invalid_inputs(self):
        cases = (
            (((Decimal("-0.01"), 1),), Decimal("0")),
            (((Decimal("1.00"), 0),), Decimal("0")),
            (((Decimal("1.00"), 1),), Decimal("1.01")),
        )
        for lines, rate in cases:
            with self.subTest(lines=lines, rate=rate):
                with self.assertRaises(ValueError):
                    total_for(lines, rate)


if __name__ == "__main__":
    unittest.main()
