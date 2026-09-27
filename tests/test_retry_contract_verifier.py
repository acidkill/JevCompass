"""Tests for the independent fictional retry contract gate."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "verify_retry_contract.py"
ORIGINAL_FIXTURE = ROOT / "tests" / "fixtures" / "retry_review"

CORRECT_RETRY_SOURCE = '''from dataclasses import dataclass, field


@dataclass
class Response:
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)


def request_with_retry(send, sleep, max_attempts=3, base_delay=0.25):
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")

    first_response = None
    for attempt in range(max_attempts):
        try:
            response = send()
        except ConnectionError:
            if attempt + 1 == max_attempts:
                raise
            sleep(base_delay)
            continue

        if first_response is None:
            first_response = response
        retryable = response.status_code == 429 or 500 <= response.status_code < 600
        if not retryable:
            return response
        if attempt + 1 == max_attempts:
            return response

        delay = base_delay
        try:
            delay = float(response.headers["Retry-After"])
        except (KeyError, TypeError, ValueError):
            pass
        sleep(delay)

    raise AssertionError("unreachable")
'''


class RetryContractVerifierTests(unittest.TestCase):
    maxDiff = None

    def run_verifier(self, fixture_dir: Path, timeout: float = 2.0):
        completed = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "--fixture-dir",
                str(fixture_dir),
                "--timeout-seconds",
                str(timeout),
            ],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        self.assertEqual(completed.stderr, "")
        self.assertLess(len(completed.stdout), 256)
        self.assertEqual(completed.stdout.count("\n"), 1)
        result = json.loads(completed.stdout)
        self.assertEqual(
            set(result), {"status", "passed", "failed", "exit_code"}
        )
        self.assertIn(result["status"], {"passed", "failed"})
        self.assertIs(type(result["passed"]), int)
        self.assertIs(type(result["failed"]), int)
        self.assertIs(type(result["exit_code"]), int)
        self.assertEqual(
            completed.returncode,
            result["exit_code"],
            msg="validator process status must agree with its JSON result",
        )
        return result, completed.stdout

    def make_fixture(self, root: Path, source: str) -> Path:
        fixture = root / "fixture"
        fixture.mkdir()
        (fixture / "retry.py").write_text(source, encoding="utf-8")
        return fixture

    def test_rejects_original_fixture_defects(self):
        result, output = self.run_verifier(ORIGINAL_FIXTURE)

        self.assertEqual(result["status"], "failed")
        self.assertGreater(result["failed"], 0)
        self.assertEqual(result["exit_code"], 1)
        self.assertNotIn(str(ORIGINAL_FIXTURE), output)
        self.assertNotIn("sleep(", output)

    def test_accepts_synthetic_contract_implementation(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.make_fixture(
                Path(temporary), CORRECT_RETRY_SOURCE + '\nprint("fixture output" * 10000)\n'
            )

            result, output = self.run_verifier(fixture)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["failed"], 0)
        self.assertGreater(result["passed"], 0)
        self.assertEqual(result["exit_code"], 0)
        self.assertNotIn("fixture output", output)
        self.assertNotIn(str(fixture), output)

    def test_rejects_status_upper_bound_mutant(self):
        mutant = CORRECT_RETRY_SOURCE.replace(
            "500 <= response.status_code < 600",
            "response.status_code >= 500",
        )
        self.assertNotEqual(mutant, CORRECT_RETRY_SOURCE)

        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.make_fixture(Path(temporary), mutant)
            result, _ = self.run_verifier(fixture)

        self.assertEqual(result["status"], "failed")
        self.assertGreater(result["failed"], 0)

    def test_rejects_first_response_returned_after_http_exhaustion(self):
        mutant = CORRECT_RETRY_SOURCE.replace(
            "if attempt + 1 == max_attempts:\n            return response",
            "if attempt + 1 == max_attempts:\n            return first_response",
        )
        self.assertNotEqual(mutant, CORRECT_RETRY_SOURCE)

        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.make_fixture(Path(temporary), mutant)
            result, _ = self.run_verifier(fixture)

        self.assertEqual(result["status"], "failed")
        self.assertGreater(result["failed"], 0)

    def test_missing_fixture_fails_with_safe_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            result, output = self.run_verifier(missing)

        self.assertEqual(
            result,
            {"status": "failed", "passed": 0, "failed": 1, "exit_code": 1},
        )
        self.assertNotIn(str(missing), output)

    def test_malformed_child_result_fails_with_safe_json(self):
        source = """import os, sys
os.write(int(sys.argv[1]), b'not-json')
raise RuntimeError('fixture failure details stay private')
"""
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.make_fixture(Path(temporary), source)
            result, output = self.run_verifier(fixture)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 1)
        self.assertNotIn(str(fixture), output)
        self.assertNotIn("fixture failure details", output)

    def test_hanging_fixture_times_out_and_fails(self):
        source = "while True:\n    pass\n"
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.make_fixture(Path(temporary), source)
            result, output = self.run_verifier(fixture, timeout=0.15)

        self.assertEqual(
            result,
            {"status": "failed", "passed": 0, "failed": 1, "exit_code": 1},
        )
        self.assertNotIn(str(fixture), output)


if __name__ == "__main__":
    unittest.main()
