"""Existing green tests for the fictional client; reviewers should find missing cases."""
import unittest

from retry import Response, request_with_retry


class RetryTests(unittest.TestCase):
    def test_success_is_single_attempt(self):
        calls = []
        sleeps = []
        result = request_with_retry(lambda: (calls.append(1) or Response(200)), sleeps.append)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleeps, [])

    def test_ordinary_client_error_is_returned(self):
        calls = []
        result = request_with_retry(lambda: (calls.append(1) or Response(404)), lambda _: None)
        self.assertEqual(result.status_code, 404)
        self.assertEqual(len(calls), 1)

    def test_server_error_then_success(self):
        responses = iter([Response(503), Response(200)])
        sleeps = []
        result = request_with_retry(lambda: next(responses), sleeps.append)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(sleeps, [0.25])


if __name__ == "__main__":
    unittest.main()
