import unittest

from query_encoder import serialize_query


class QueryEncoderTests(unittest.TestCase):
    def test_v2_space_encoding(self):
        self.assertEqual(serialize_query([("q", "blue sky")]), "q=blue+sky")

    def test_literal_plus_remains_escaped(self):
        self.assertEqual(serialize_query([("tag", "a+b")]), "tag=a%2Bb")

    def test_pair_order_and_empty_values_are_preserved(self):
        self.assertEqual(
            serialize_query([("b", "2"), ("a", "")]),
            "b=2&a=",
        )


if __name__ == "__main__":
    unittest.main()
