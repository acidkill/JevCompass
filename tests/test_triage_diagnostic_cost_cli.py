"""Allowlisted diagnostic costs reach triage without changing causal semantics."""
import contextlib
import io
import json
import unittest
from unittest import mock

from jevcompass.cli import main


class DiagnosticCostCliTests(unittest.TestCase):
    def argv(self):
        return ["triage", "--exit-code", "1", "--kind", "timeout",
                "--hypothesis", "timeout_contention", "--hypothesis", "timeout_nonterminating",
                "--json"]

    def test_costs_reach_safe_metadata_and_preserve_failed_test(self):
        output = io.StringIO()
        with mock.patch("jevcompass.triage.DecisionsClient") as client, contextlib.redirect_stdout(output):
            client.return_value.decide.return_value = {
                "diagnostic": {"type": "choice", "choice": "timeout_nonterminating", "confidence": 0.9}}
            self.assertEqual(main(self.argv() + ["--diagnostic-cost", "timeout_contention=high",
                                                "--diagnostic-cost", "timeout_nonterminating=low"]), 0)
        state = client.return_value.decide.call_args.args[0]
        self.assertEqual(state["diagnostic_costs"], {"timeout_contention": "high", "timeout_nonterminating": "low"})
        result = json.loads(output.getvalue())
        self.assertTrue(result["test_failed"])
        self.assertEqual(result["observed_exit_status"], 1)
        self.assertEqual(result["hypothesis_ranking_status"], "not_established")
        self.assertFalse(result["executed"])

    def test_bad_costs_are_rejected_before_backend(self):
        for options in (
            ["--diagnostic-cost", "timeout_contention=PRIVATE_TEXT"],
            ["--diagnostic-cost", "import_path_changed=low"],
            ["--diagnostic-cost", "timeout_contention=low", "--diagnostic-cost", "timeout_contention=high"],
        ):
            with self.subTest(options=options), mock.patch("jevcompass.triage.DecisionsClient") as client:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                    main(self.argv() + options)
                self.assertEqual(raised.exception.code, 2)
                client.assert_not_called()

    def test_locally_resolved_wait_does_not_call_backend_with_costs(self):
        with mock.patch("jevcompass.triage.DecisionsClient") as client, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(self.argv() + ["--timeout-observation", "wait_condition_unsatisfiable",
                                                "--diagnostic-cost", "timeout_nonterminating=low"]), 0)
        client.assert_not_called()
