"""Public API contract tests for the fictional ParcelCache codec."""
from __future__ import annotations

import unittest

from parcelcache.api import decode, encode


class ParcelCacheContractTests(unittest.TestCase):
    def test_round_trip(self):
        record = {"parcel": "PX-204", "weight_kg": 2.5}
        self.assertEqual(decode(encode(record)), record)

    def test_decode_rejects_non_object_json(self):
        with self.assertRaisesRegex(ValueError, "record-must-be-json-object"):
            decode(b"[]")


if __name__ == "__main__":
    unittest.main()
