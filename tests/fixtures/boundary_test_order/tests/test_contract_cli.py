import json
import subprocess
import sys
import unittest


class PublicRepresentationTests(unittest.TestCase):
    def test_money_representation_json_contract(self):
        for weight, zone, total in [(1000, "near", 375), (1001, "near", 500), (2000, "far", 950)]:
            with self.subTest(weight=weight, zone=zone):
                run = subprocess.run([sys.executable, "-m", "parcelquote",
                                      "--weight-grams", str(weight), "--zone", zone],
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(run.stderr, "")
                self.assertEqual(json.loads(run.stdout), {
                    "weight_grams": weight, "zone": zone,
                    "billable_kg": (weight + 999) // 1000,
                    "total_cents": total,
                    "money": {"currency": "USD", "minor_units": total},
                })

    def test_invalid_input_has_no_json(self):
        run = subprocess.run([sys.executable, "-m", "parcelquote",
                              "--weight-grams", "0", "--zone", "near"],
                             capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "")
        self.assertTrue(run.stderr)
