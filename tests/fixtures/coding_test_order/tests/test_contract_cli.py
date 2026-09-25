import json
import subprocess
import sys
import unittest


class ParcelQuoteCliContractTests(unittest.TestCase):
    def test_partial_kilogram_json_contract(self):
        result = subprocess.run(
            [sys.executable, "-m", "parcelquote", "--weight-grams", "1001", "--zone", "near"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "weight_grams": 1001,
            "zone": "near",
            "billable_kg": 2,
            "total_cents": 500,
        })

    def test_invalid_zone_is_rejected_without_json_output(self):
        result = subprocess.run(
            [sys.executable, "-m", "parcelquote", "--weight-grams", "1000", "--zone", "ocean"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
