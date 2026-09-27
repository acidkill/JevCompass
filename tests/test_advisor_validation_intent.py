"""Required diff validation does not override the requested implementation task."""
import unittest
from unittest import mock
from jevcompass import advisor, strategy

class ValidationIntentTests(unittest.TestCase):
    def test_required_diff_check_preserves_primary_task(self):
        cases = (
            ("Change the behavior of existing function normalize_labels in labels.py to preserve order. Run python -m unittest discover -s tests -v and git diff --check.", "coding"),
            ("Implement the requested Python feature and run git diff --check after the changes.", "coding"),
            ("Plan how to implement the requested feature and include git diff --check in required validation.", "planning"),
            ("Review the changed function and the git diff --check output for correctness.", "review"),
            ("Inspect the git diff --stat output for this implementation and assess the change.", "review"),
            ("Do not change the behavior of the existing function. Review its implementation instead.", "review"),
        )
        for prompt, expected in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(advisor.classify_task(prompt)[0], expected)

    def test_behavior_change_with_required_checks_enters_opt_in_strategy_route(self):
        prompt = "Change the behavior of existing function normalize_labels in labels.py to preserve order. Run python -m unittest discover -s tests -v and git diff --check."
        result = strategy.StrategyResult((), "no-remote-choice")
        with mock.patch.object(strategy, "choose_strategies", return_value=result) as choose, mock.patch.object(advisor, "select_advice", return_value=None) as select:
            advisor.evaluate({"hook_event_name": "UserPromptSubmit", "prompt": prompt}, strategy_advice=True)
        choose.assert_called_once()
        self.assertEqual({s.value for s in choose.call_args.args[1]}, {"existing_symbol", "behavior_change"})
        self.assertIs(select.call_args.kwargs["local_only"], True)
