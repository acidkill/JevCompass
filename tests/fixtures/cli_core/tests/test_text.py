import unittest

from tinytext.text import normalize_whitespace


class NormalizeWhitespaceTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(normalize_whitespace(""), "")

    def test_repeated_spaces(self):
        self.assertEqual(normalize_whitespace("  red   fox  "), "red fox")


if __name__ == "__main__":
    unittest.main()
