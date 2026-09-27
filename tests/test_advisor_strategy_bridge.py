"""Opt-in task strategy bridge remains private, bounded, and default-off."""

import unittest
from unittest import mock

from jevcompass import advisor, strategy


class StrategyBridgeTests(unittest.TestCase):
    def test_extractor_is_request_scope_and_never_infers_failure_evidence(self):
        signals = advisor._prompt_strategy_signals(
            "Implement a Python change to the behavior of the existing function normalize: preserve output ordering."
        )
        self.assertEqual({signal.value for signal in signals}, {"existing_symbol", "behavior_change"})
        self.assertNotIn("failing_test", {signal.value for signal in signals})
        self.assertEqual(advisor._prompt_strategy_signals("Implement this small update."), ())
        self.assertEqual(advisor._prompt_strategy_signals("Do not modify the existing function normalize."), ())
        self.assertEqual(advisor._prompt_strategy_signals("Keep the existing function output stable."), ())
        self.assertEqual(advisor._prompt_strategy_signals("Do not change output behavior."), ())
        self.assertEqual(advisor._prompt_strategy_signals("Do not upgrade the dependency."), ())
        self.assertEqual(advisor._prompt_strategy_signals("Rename a label. Do not touch the existing function."), ())

    def test_opt_in_coding_bridge_makes_one_strategy_call_and_labels_cached_choice(self):
        from jevcompass.strategy import StrategyId, StrategyRecommendation, StrategyResult

        prompt = (
            "Implement a Python change to the behavior of the existing function normalize: "
            "preserve output ordering. private-sentinel-bridge"
        )
        result = StrategyResult(
            (StrategyRecommendation(StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT,
                                    "Define expected behavior before changing the implementation."),),
            "remote-choice", usage=None, cache_hit=True,
        )
        generic = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                  "additionalContext": "JevCompass advice ID: deadbeef\\nCandidate IDs: exec_command"}}
        with mock.patch.object(strategy, "choose_strategies", return_value=result) as choose, \
                mock.patch.object(advisor, "select_advice", return_value=generic) as select, \
                mock.patch.object(advisor, "_judge") as judge:
            output = advisor.evaluate({"hook_event_name": "UserPromptSubmit", "prompt": prompt},
                                       trace="deadbeef", strategy_advice=True)
        choose.assert_called_once()
        self.assertEqual(choose.call_args.args[0].value, "coding")
        self.assertEqual({s.value for s in choose.call_args.args[1]}, {"existing_symbol", "behavior_change"})
        self.assertNotIn("private-sentinel-bridge", repr(choose.call_args))
        self.assertIs(select.call_args.kwargs["local_only"], True)
        judge.assert_not_called()
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("strategy advice (cached", context)
        self.assertIn("define_contract_then_implement", context)
        self.assertIn("Candidate IDs: exec_command", context)
        self.assertNotIn("private-sentinel-bridge", context)
        self.assertIn("Signals reflect the request wording", context)
        self.assertIn("Cached selection made no new API call", context)

    def test_strategy_bridge_is_default_off_and_unknown_signal_falls_back(self):
        prompt = "Implement a Python change to the behavior of the existing function normalize."
        with mock.patch.object(strategy, "choose_strategies") as choose, \
                mock.patch.object(advisor, "select_advice", return_value=None) as select:
            advisor.evaluate({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
            advisor.evaluate({"hook_event_name": "UserPromptSubmit",
                               "prompt": "Implement this small update."}, strategy_advice=True)
        choose.assert_not_called()
        self.assertEqual(select.call_count, 2)
        self.assertNotIn("local_only", select.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
