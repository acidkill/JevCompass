"""Diagnostic validity is independent of prospective causal ranking validity."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pilot_contract_triage_pair as runner


class ProfileRankDimensionsTests(unittest.TestCase):
    def test_diagnostic_survives_unestablished_partial_and_invalid_rank(self):
        profile = SimpleNamespace(
            triage_accepted_ids=('assertion_behavior_regression', 'confirm_behavior_contract'),
            triage_accepted_statuses=('no-remote-choice',),
            triage_hypotheses=('assertion_behavior_regression', 'assertion_expectation_drift', 'confirm_behavior_contract'),
            rank_hypotheses=True,
        )
        for status, order, expected in (
            ('not_established', [], 'not_established'),
            ('incomplete', [], 'incomplete'),
            ('complete', ['confirm_behavior_contract', 'assertion_behavior_regression'], 'incomplete'),
            ('complete', ['assertion_behavior_regression'] * 2, 'incomplete'),
            ('complete', ['assertion_expectation_drift', 'assertion_behavior_regression'], 'complete'),
        ):
            with self.subTest(status=status, order=order):
                payload = dict(observed_exit_status=1, test_failed=True, status='no-remote-choice',
                               steps=[{'id': 'confirm_behavior_contract'}], executed=False,
                               decision_usage=None, hypothesis_order=order,
                               hypothesis_ranking_status=status)
                result = runner._validated_triage(json.dumps(payload), 1, profile)
                self.assertEqual(result['candidate_ids'], ['confirm_behavior_contract'])
                self.assertEqual(result['hypothesis_ranking_status'], expected)
                self.assertEqual(result['hypothesis_order'], order if expected == 'complete' else [])

    def test_invalid_diagnostic_is_not_rescued_by_complete_rank(self):
        profile = SimpleNamespace(triage_accepted_ids=('assertion_behavior_regression',),
                                  triage_accepted_statuses=('no-remote-choice',),
                                  triage_hypotheses=('assertion_behavior_regression', 'assertion_expectation_drift'),
                                  rank_hypotheses=True)
        payload = dict(observed_exit_status=0, test_failed=True, status='no-remote-choice',
                       steps=[{'id': 'assertion_behavior_regression'}], executed=False,
                       hypothesis_order=list(profile.triage_hypotheses), hypothesis_ranking_status='complete')
        self.assertEqual(runner._validated_triage(json.dumps(payload), 1, profile)['status'], 'unscored')

    def test_diagnostic_candidate_requested_without_changing_causal_profile(self):
        profile = SimpleNamespace(
            triage_kinds=("assertion",),
            triage_hypotheses=("assertion_behavior_regression", "assertion_expectation_drift"),
            triage_accepted_ids=("assertion_behavior_regression", "confirm_behavior_contract"),
            rank_hypotheses=True, triage_observations={},
        )
        argv = runner._triage_argv(1, profile)
        self.assertIn("confirm_behavior_contract", argv)
        self.assertEqual(argv.count("confirm_behavior_contract"), 1)
        self.assertNotIn("confirm_behavior_contract", profile.triage_hypotheses)
