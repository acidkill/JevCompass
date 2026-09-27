#!/usr/bin/env python3
"""Verify a fictional retry.py against an independent local contract suite.

The fixture is executed as trusted synthetic code in a time-bounded subprocess;
this process boundary is not a security sandbox. Fixture stdout and stderr are
discarded, and only a small machine-generated test summary is read back.
"""
from __future__ import annotations

import argparse
import json
from contextlib import redirect_stderr
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

DEFAULT_TIMEOUT_SECONDS = 2.0
MAX_TIMEOUT_SECONDS = 30.0
MAX_RESULT_BYTES = 4096
EXPECTED_TEST_COUNT = 10

# This suite is embedded in the verifier and never loaded from the fixture.
# Keep its only output on the dedicated result descriptor; arbitrary fixture
# prints go to DEVNULL in the parent process.
_CHILD_SUITE = r'''
import json
import os
import sys
import unittest

result_fd = int(sys.argv[1])


def report(tests_run, failures):
    payload = json.dumps(
        {"tests_run": tests_run, "failures": failures},
        separators=(",", ":"),
    ).encode("ascii")
    os.write(result_fd, payload)


try:
    sys.path.insert(0, os.getcwd())
    from retry import Response, request_with_retry
except BaseException:
    report(0, 1)
    raise SystemExit(1)


class RetryContractTests(unittest.TestCase):
    def test_status_retry_boundaries(self):
        for status in (499, 500, 599, 600):
            with self.subTest(status=status):
                first = Response(status)
                second = Response(200)
                responses = [first, second]
                calls = []
                sleeps = []

                def send():
                    calls.append(None)
                    return responses.pop(0)

                result = request_with_retry(send, sleeps.append, base_delay=0.4)

                if status in (499, 600):
                    self.assertIs(result, first)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(sleeps, [])
                else:
                    self.assertIs(result, second)
                    self.assertEqual(len(calls), 2)
                    self.assertEqual(sleeps, [0.4])

    def test_429_is_retried(self):
        retry_response = Response(429)
        success = Response(200)
        responses = iter((retry_response, success))
        sleeps = []
        calls = []

        def send():
            calls.append(None)
            return next(responses)

        self.assertIs(request_with_retry(send, sleeps.append), success)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [0.25])

    def test_exhausted_http_attempts_return_last_response_without_final_sleep(self):
        responses = [Response(503), Response(500), Response(599)]
        sleeps = []
        calls = []

        def send():
            calls.append(None)
            return responses[len(calls) - 1]

        result = request_with_retry(send, sleeps.append, max_attempts=3)

        self.assertIs(result, responses[-1])
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [0.25, 0.25])

    def test_connection_error_is_retried(self):
        failure = ConnectionError("temporary")
        calls = []
        sleeps = []

        def send():
            calls.append(None)
            if len(calls) == 1:
                raise failure
            return Response(200)

        result = request_with_retry(send, sleeps.append)

        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [0.25])

    def test_exhausted_connection_errors_raise_last_instance_without_final_sleep(self):
        failures = [ConnectionError("one"), ConnectionError("two"), ConnectionError("three")]
        calls = []
        sleeps = []

        def send():
            failure = failures[len(calls)]
            calls.append(None)
            raise failure

        with self.assertRaises(ConnectionError) as caught:
            request_with_retry(send, sleeps.append, max_attempts=3)

        self.assertIs(caught.exception, failures[-1])
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [0.25, 0.25])

    def test_other_exceptions_propagate_immediately(self):
        failure = ValueError("do not retry this")
        calls = []
        sleeps = []

        def send():
            calls.append(None)
            raise failure

        with self.assertRaises(ValueError) as caught:
            request_with_retry(send, sleeps.append, max_attempts=4)

        self.assertIs(caught.exception, failure)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_numeric_retry_after_controls_delay(self):
        retry_response = Response(429, {"Retry-After": "1.75"})
        success = Response(200)
        responses = iter((retry_response, success))
        sleeps = []

        result = request_with_retry(lambda: next(responses), sleeps.append)

        self.assertIs(result, success)
        self.assertEqual(sleeps, [1.75])

    def test_missing_or_non_numeric_retry_after_uses_base_delay(self):
        for headers in ({}, {"Retry-After": "later"}):
            with self.subTest(headers=headers):
                responses = iter((Response(503, headers), Response(200)))
                sleeps = []

                result = request_with_retry(
                    lambda: next(responses), sleeps.append, base_delay=0.6
                )

                self.assertEqual(result.status_code, 200)
                self.assertEqual(sleeps, [0.6])

    def test_max_attempts_is_a_total_call_limit(self):
        responses = [Response(503), Response(503), Response(503), Response(503)]
        calls = []
        sleeps = []

        def send():
            calls.append(None)
            return responses[len(calls) - 1]

        result = request_with_retry(send, sleeps.append, max_attempts=4)

        self.assertIs(result, responses[-1])
        self.assertEqual(len(calls), 4)
        self.assertEqual(sleeps, [0.25, 0.25, 0.25])

    def test_attempt_count_below_one_is_rejected_before_calling(self):
        for attempts in (0, -1):
            with self.subTest(max_attempts=attempts):
                calls = []
                sleeps = []

                def send():
                    calls.append(None)
                    return Response(200)

                with self.assertRaises(ValueError):
                    request_with_retry(
                        send, sleeps.append, max_attempts=attempts
                    )

                self.assertEqual(calls, [])
                self.assertEqual(sleeps, [])


suite = unittest.defaultTestLoader.loadTestsFromTestCase(RetryContractTests)
result = unittest.TextTestRunner(
    stream=open(os.devnull, "w"), verbosity=0
).run(suite)
report(result.testsRun, len(result.failures) + len(result.errors))
raise SystemExit(0 if result.wasSuccessful() else 1)
'''


def _safe_result(status: str, passed: int, failed: int) -> dict[str, object]:
    return {
        "status": status,
        "passed": passed,
        "failed": failed,
        "exit_code": 0 if status == "passed" else 1,
    }


def _parse_child_result(payload: bytes) -> tuple[int, int] | None:
    if not payload or len(payload) > MAX_RESULT_BYTES:
        return None
    try:
        result = json.loads(payload.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(result, dict)
        or set(result) != {"tests_run", "failures"}
        or type(result["tests_run"]) is not int
        or type(result["failures"]) is not int
        or result["tests_run"] < 0
        or result["failures"] < 0
    ):
        return None
    return result["tests_run"], result["failures"]


def _run_contract(fixture_dir: Path, timeout: float) -> dict[str, object]:
    retry_file = fixture_dir / "retry.py"
    if not fixture_dir.is_dir() or not retry_file.is_file():
        return _safe_result("failed", 0, 1)

    read_fd, write_fd = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                _CHILD_SUITE,
                str(write_fd),
            ],
            cwd=fixture_dir,
            env={},
            pass_fds=(write_fd,),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except (OSError, ValueError):
        os.close(read_fd)
        os.close(write_fd)
        return _safe_result("failed", 0, 1)
    finally:
        if process is not None:
            os.close(write_fd)

    try:
        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return _safe_result("failed", 0, 1)

        payload = os.read(read_fd, MAX_RESULT_BYTES + 1)
        parsed = _parse_child_result(payload)
        if parsed is None:
            return _safe_result("failed", 0, 1)

        tests_run, failures = parsed
        passed = max(0, tests_run - failures)
        if (
            return_code != 0
            or failures != 0
            or tests_run != EXPECTED_TEST_COUNT
        ):
            return _safe_result("failed", passed, max(failures, 1))
        return _safe_result("passed", passed, 0)
    finally:
        os.close(read_fd)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--fixture-dir", required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    try:
        with open(os.devnull, "w", encoding="utf-8") as discarded:
            with redirect_stderr(discarded):
                return parser.parse_args(argv)
    except SystemExit:
        return None


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args is None:
        result = _safe_result("failed", 0, 1)
    elif (
        not (0 < args.timeout_seconds <= MAX_TIMEOUT_SECONDS)
        or not isinstance(args.timeout_seconds, float)
    ):
        result = _safe_result("failed", 0, 1)
    else:
        try:
            fixture_dir = Path(args.fixture_dir).resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            result = _safe_result("failed", 0, 1)
        else:
            result = _run_contract(fixture_dir, args.timeout_seconds)

    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
