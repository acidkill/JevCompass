"""CLI contract for diagnostic advice after an observed test exit."""

import contextlib
import io
import json
import unittest
from unittest import mock

from jevcompass.cli import main
from jevcompass.decisions import DecisionsClient


class TriageCliTests(unittest.TestCase):
    def test_ambiguous_failure_keeps_original_exit_and_local_step_text(self):
        output = io.StringIO()
        with mock.patch("jevcompass.triage.DecisionsClient") as client, contextlib.redirect_stdout(output):
            client.return_value.decide.return_value = {
                "diagnostic": {"type": "choice", "choice": "import_path_changed", "confidence": 0.9}
            }
            exit_code = main(["triage", "--exit-code", "1", "--kind", "import",
                              "--hypothesis", "import_module_missing",
                              "--hypothesis", "import_path_changed", "--json"])
        result = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)  # CLI advice succeeded, the test did not.
        self.assertTrue(result["test_failed"])
        self.assertEqual(result["observed_exit_status"], 1)
        self.assertEqual(result["status"], "remote-choice")
        self.assertEqual(result["steps"][0]["id"], "import_path_changed")
        self.assertFalse(result["executed"])

    def test_json_reports_provider_usage_without_response_identifiers(self):
        payload = json.dumps({
            "answers": {"diagnostic": {"type": "choice", "choice": "import_path_changed",
                                        "confidence": 0.9}},
            "usage": {"input_tokens": 100, "output_tokens": 12, "cost": 0.00002},
            "id": "PRIVATE_GENERATION_ID", "provider": "PRIVATE_PROVIDER",
        }).encode()
        real = DecisionsClient(api_key="synthetic", transport=lambda *args: payload)
        output = io.StringIO()
        with mock.patch("jevcompass.triage.DecisionsClient", return_value=real), \
             contextlib.redirect_stdout(output):
            main(["triage", "--exit-code", "1", "--kind", "import",
                  "--hypothesis", "import_module_missing", "--hypothesis", "import_path_changed", "--json"])
        result = json.loads(output.getvalue())
        self.assertEqual(result["decision_usage"], {
            "input_tokens": 100, "output_tokens": 12, "cost_usd": 0.00002,
        })
        self.assertEqual(result["observed_exit_status"], 1)
        self.assertNotIn("PRIVATE_", output.getvalue())

    def test_successful_test_never_calls_backend(self):
        output = io.StringIO()
        with mock.patch("jevcompass.triage.DecisionsClient") as client, contextlib.redirect_stdout(output):
            main(["triage", "--exit-code", "0", "--kind", "import",
                  "--hypothesis", "import_module_missing", "--hypothesis", "import_path_changed", "--json"])
        self.assertFalse(json.loads(output.getvalue())["test_failed"])
        client.assert_not_called()

    def test_confirmed_import_layout_skips_backend_and_rules_out_missing_package(self):
        output = io.StringIO()
        with mock.patch("jevcompass.triage.DecisionsClient") as client, contextlib.redirect_stdout(output):
            exit_code = main([
                "triage", "--exit-code", "1", "--kind", "import",
                "--hypothesis", "import_module_missing",
                "--hypothesis", "import_path_changed",
                "--import-observation", "package_present",
                "--import-observation", "target_module_absent",
                "--import-observation", "replacement_module_present",
                "--json",
            ])
        result = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertFalse(result["executed"])
        self.assertIsNone(result["decision_usage"])
        self.assertEqual(result["status"], "no-remote-choice")
        self.assertEqual(result["observed_exit_status"], 1)
        self.assertEqual([step["id"] for step in result["steps"]], ["import_path_changed"])
        client.assert_not_called()

    def test_invalid_import_observation_is_rejected_by_cli(self):
        output = io.StringIO()
        with mock.patch("jevcompass.triage.DecisionsClient") as client, \
             contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as caught:
            main([
                "triage", "--exit-code", "1", "--kind", "import",
                "--hypothesis", "import_module_missing",
                "--hypothesis", "import_path_changed",
                "--import-observation", "/private/client/repo",
                "--json",
            ])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("invalid choice", output.getvalue())
        client.assert_not_called()

    def test_underspecified_contract_returns_local_step_without_provider_call(self):
        output = io.StringIO()
        with mock.patch("jevcompass.triage.DecisionsClient") as client, contextlib.redirect_stdout(output):
            status = main([
                "triage", "--exit-code", "1", "--kind", "assertion",
                "--hypothesis", "assertion_expectation_drift",
                "--hypothesis", "assertion_behavior_regression",
                "--hypothesis", "confirm_behavior_contract",
                "--assertion-observation", "contract_underspecified", "--json",
            ])
        result = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(result["status"], "no-remote-choice")
        self.assertEqual([step["id"] for step in result["steps"]],
                         ["confirm_behavior_contract"])
        client.assert_not_called()

    def test_fixture_conflict_remote_request_contains_only_allowlisted_observation(self):
        output = io.StringIO()
        with mock.patch("jevcompass.triage.DecisionsClient") as client, contextlib.redirect_stdout(output):
            client.return_value.decide.return_value = {
                "diagnostic": {"type": "choice", "choice": "assertion_expectation_drift",
                               "confidence": 0.9}
            }
            main([
                "triage", "--exit-code", "1", "--kind", "assertion",
                "--hypothesis", "assertion_expectation_drift",
                "--hypothesis", "assertion_behavior_regression",
                "--assertion-observation", "legacy_fixture_conflict", "--json",
            ])
        state = client.return_value.decide.call_args.args[0]
        self.assertEqual(state["assertion_observations"], ["legacy_fixture_conflict"])
        self.assertEqual(set(state), {"test_outcome", "failure_kinds", "hypotheses",
                                      "assertion_observations"})

    def test_invalid_assertion_observation_is_rejected(self):
        stderr = io.StringIO()
        with (mock.patch("jevcompass.triage.DecisionsClient") as client,
              contextlib.redirect_stderr(stderr),
              self.assertRaises(SystemExit) as caught):
            main([
                "triage", "--exit-code", "1", "--kind", "assertion",
                "--hypothesis", "assertion_expectation_drift",
                "--assertion-observation", "/private/client/repo", "--json",
            ])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("invalid choice", stderr.getvalue())
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
