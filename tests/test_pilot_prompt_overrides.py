"""Per-run prompt isolation without live agents or private credentials."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('isolated_prompt_runner', ROOT / 'scripts/pilot_test_order_pair.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class PromptOverrideTests(unittest.TestCase):
    def test_overrides_are_per_run_and_defaults_remain_unchanged(self):
        original = (runner.BASE_PROMPT, runner.TREATMENT_RANKING)
        prompts = []
        def arm(**kwargs):
            prompts.append(kwargs['prompt'])
            return {'cli_status': 'completed', 'required_suite_exit': 0,
                    'focused_test_exits': [{'exit_code': 0}]}
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            runner.core, '_copy_auth', return_value=True
        ), mock.patch.object(runner, '_run_arm', side_effect=arm):
            common = dict(codex='unused', model='unused', reasoning_effort='low', seed=1)
            runner.run_pair(**common, output_dir=Path(temporary)/'custom',
                            baseline_prompt='Shared reviewed facts.',
                            treatment_prompt_suffix=' Optional ranking.')
            self.assertCountEqual(prompts, ['Shared reviewed facts.',
                                            'Shared reviewed facts. Optional ranking.'])
            prompts.clear()
            runner.run_pair(**common, output_dir=Path(temporary)/'default')
            self.assertCountEqual(prompts, [original[0], original[0]+original[1]])
        self.assertEqual((runner.BASE_PROMPT, runner.TREATMENT_RANKING), original)

    def test_invalid_overrides_fail_before_auth_or_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            runner.core, '_copy_auth'
        ) as auth:
            output = Path(temporary)/'never-created'
            for key, values in [('baseline_prompt', ['', ' ', 12, 'a'*32769]),
                                ('treatment_prompt_suffix', ['', ' ', [], 'a'*16385])]:
                for value in values:
                    with self.subTest(key=key, value_type=type(value).__name__):
                        with self.assertRaises(ValueError):
                            runner.run_pair(codex='unused', model='unused', reasoning_effort='low',
                                            output_dir=output, **{key:value})
            auth.assert_not_called()
            self.assertFalse(output.exists())
