"""Installer and CLI contract for the explicit strategy-hook opt-in."""

import unittest
from unittest import mock

from jevcompass import advisor, installer


class StrategyHookInstallTests(unittest.TestCase):
    def test_opt_in_is_only_on_user_prompt_hook_and_survives_reinstall(self):
        base = {"hooks": {}}
        standard = advisor.hook_command()
        enabled = installer.merge_hooks(base, standard, strategy_advice=True)
        prompt_command = enabled["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        child_command = enabled["hooks"]["SubagentStart"][0]["hooks"][0]["command"]
        self.assertEqual(prompt_command, advisor.hook_command(strategy_advice=True))
        self.assertEqual(child_command, standard)
        self.assertEqual(installer.merge_hooks(enabled, standard), enabled)
        disabled = installer.merge_hooks(enabled, standard, strategy_advice=False)
        self.assertEqual(disabled["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"], standard)

    def test_custom_command_and_malformed_unrelated_handlers_are_preserved(self):
        custom = "/opt/custom/codex-jev-advisor"
        malformed = {"type": "command", "command": "other-tool 'unterminated"}
        base = {"hooks": {"UserPromptSubmit": [{"hooks": [malformed]}]}}
        installed = installer.merge_hooks(base, custom, strategy_advice=True)
        self.assertEqual(installed["hooks"]["UserPromptSubmit"][0]["hooks"][0], malformed)
        own = installed["hooks"]["UserPromptSubmit"][1]["hooks"][0]["command"]
        self.assertEqual(own, custom)
        self.assertEqual(installer.merge_hooks(installed, custom), installed)

    def test_module_hook_dispatch_forwards_opt_in_without_cli(self):
        from jevcompass import __main__ as module

        with mock.patch.object(advisor, "hook_main", return_value=0) as hook:
            self.assertEqual(module.main(["hook", "--strategy-advice"]), 0)
            hook.assert_called_once_with(strategy_advice=True)
            hook.reset_mock()
            self.assertEqual(module.main(["hook"]), 0)
            hook.assert_called_once_with(strategy_advice=False)

    def test_default_hook_command_and_strategy_flags_are_explicit(self):
        self.assertNotIn("--strategy-advice", advisor.hook_command())
        self.assertIn("--strategy-advice", advisor.hook_command(strategy_advice=True))
        with self.assertRaises(SystemExit):
            from jevcompass.cli import main
            main(["install", "--strategy-advice", "--disable-strategy-advice"])


if __name__ == "__main__":
    unittest.main()
