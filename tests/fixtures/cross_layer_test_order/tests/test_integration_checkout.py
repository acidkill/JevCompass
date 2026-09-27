import json
import subprocess
import sys
import unittest


class CheckoutCliIntegrationTests(unittest.TestCase):
    def test_success_json_contract(self):
        run = subprocess.run(
            [sys.executable, "-m", "checkout", "--subtotal-cents", "1250"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stderr, "")
        self.assertEqual(
            json.loads(run.stdout),
            {
                "subtotal_cents": 1250,
                "service_fee_cents": 25,
                "total_cents": 1275,
            },
        )

    def test_negative_subtotal_emits_no_json(self):
        run = subprocess.run(
            [sys.executable, "-m", "checkout", "--subtotal-cents", "-1"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, "")
        self.assertIn("subtotal_cents must be nonnegative", run.stderr)


if __name__ == "__main__":
    unittest.main()
