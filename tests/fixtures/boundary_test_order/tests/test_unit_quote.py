import unittest

from parcelquote import quote_shipping


class QuoteShippingUnitTests(unittest.TestCase):
    def test_exact_kilogram_is_charged_once(self):
        quote = quote_shipping(1000, "near")
        self.assertEqual((quote.billable_kg, quote.total_cents), (1, 375))

    def test_partial_kilogram_rounds_up(self):
        quote = quote_shipping(1001, "near")
        self.assertEqual((quote.billable_kg, quote.total_cents), (2, 500))

    def test_far_zone_uses_its_base_charge(self):
        quote = quote_shipping(2000, "far")
        self.assertEqual(quote.total_cents, 950)

    def test_nonpositive_weight_is_rejected(self):
        for weight in (0, -1):
            with self.subTest(weight=weight), self.assertRaises(ValueError):
                quote_shipping(weight, "near")


if __name__ == "__main__":
    unittest.main()
