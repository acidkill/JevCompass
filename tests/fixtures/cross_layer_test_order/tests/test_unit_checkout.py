import unittest

from checkout import quote_checkout


class CheckoutUnitTests(unittest.TestCase):
    def test_half_cent_rounds_half_up(self):
        quote = quote_checkout(25)
        self.assertEqual(
            (quote.subtotal_cents, quote.service_fee_cents, quote.total_cents),
            (25, 1, 26),
        )

    def test_one_and_a_half_cents_rounds_up(self):
        quote = quote_checkout(75)
        self.assertEqual((quote.service_fee_cents, quote.total_cents), (2, 77))

    def test_nonnegative_integer_validation(self):
        for invalid in (-1,):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                quote_checkout(invalid)
        for invalid in (True, 1.5):
            with self.subTest(value=invalid), self.assertRaises(TypeError):
                quote_checkout(invalid)


if __name__ == "__main__":
    unittest.main()
