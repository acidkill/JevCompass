"""Offline tests for the isolated UserPromptSubmit canary probe."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "probe_hook_delivery.py"
SPEC = importlib.util.spec_from_file_location("probe_hook_delivery", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(probe)


class HookDeliveryProbeTests(unittest.TestCase):
    def test_hook_injects_only_for_user_prompt_submit(self):
        canary = "a1b2c3d4e5f60718293a4b5c"
        output = probe.hook_output(canary)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn(canary, context)
        with self.assertRaises(ValueError):
            probe.hook_output("not-a-random-token")

    def test_hook_main_ignores_non_prompt_events(self):
        import io

        output = io.StringIO()
        probe.hook_main(
            "a1b2c3d4e5f60718293a4b5c",
            stdin=io.StringIO('{"hook_event_name":"SubagentStart"}'),
            stdout=output,
        )
        self.assertEqual(output.getvalue(), "")

    def test_subagent_hook_emits_only_for_explorer_start(self):
        import io

        token = "a1b2c3d4e5f60718293a4b5c"
        output = io.StringIO()
        probe.subagent_hook_main(
            token,
            stdin=io.StringIO('{"hook_event_name":"SubagentStart","agent_type":"explorer"}'),
            stdout=output,
        )
        hook_output = json.loads(output.getvalue())["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "SubagentStart")
        self.assertIn(token, hook_output["additionalContext"])
        output = io.StringIO()
        probe.subagent_hook_main(
            token,
            stdin=io.StringIO('{"hook_event_name":"SubagentStart","agent_type":"worker"}'),
            stdout=output,
        )
        self.assertEqual(output.getvalue(), "")
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt"
            output = io.StringIO()
            probe.subagent_hook_main(
                token,
                stdin=io.StringIO('{"hook_event_name":"SubagentStart","agent_type":"explorer"}'),
                stdout=output,
                receipt_path=receipt,
            )
            self.assertEqual(receipt.read_text(), "matched")
            self.assertIn(token, output.getvalue())

    def test_canary_must_be_in_first_assistant_before_first_tool(self):
        token = "a1b2c3d4e5f60718293a4b5c"
        assistant = json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": f"{token} ready"},
        })
        tool = json.dumps({
            "type": "item.started",
            "item": {"type": "command_execution", "name": "exec_command"},
        })
        result = probe.summarize_stream([assistant, tool], token)
        self.assertTrue(result["canary_before_first_tool"])
        self.assertFalse(probe.summarize_stream([tool, assistant], token)["canary_before_first_tool"])
        without_first_echo = json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "I will inspect the file."},
        })
        self.assertFalse(
            probe.summarize_stream([without_first_echo, assistant, tool], token)["canary_before_first_tool"]
        )

    def test_subagent_summary_requires_explorer_identity_and_first_tool_order(self):
        token = "a1b2c3d4e5f60718293a4b5c"
        parent = json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": f"{token} parent response"},
        })
        child = json.dumps({
            "type": "item.completed", "agent_type": "explorer",
            "item": {"type": "agent_message", "text": f"{token} child response"},
        })
        tool = json.dumps({
            "type": "item.started", "agent_type": "explorer",
            "item": {"type": "command_execution", "name": "exec_command"},
        })
        summary = probe.summarize_subagent_stream([parent, child, tool], token)
        self.assertTrue(summary["child_observed"])
        self.assertTrue(summary["canary_before_first_tool"])
        self.assertFalse(probe.summarize_subagent_stream([parent], token)["child_observed"])
        self.assertFalse(probe.summarize_subagent_stream([tool, child], token)["canary_before_first_tool"])
        child_by_id = json.dumps({
            "type": "item.completed", "agent_id": "synthetic-child",
            "item": {"type": "agent_message", "text": f"{token} child response"},
        })
        tool_by_id = json.dumps({
            "type": "item.started", "agent_id": "synthetic-child",
            "item": {"type": "command_execution", "name": "exec_command"},
        })
        self.assertTrue(
            probe.summarize_subagent_stream([child_by_id, tool_by_id], token)["canary_before_first_tool"]
        )

    def test_fake_cli_exercises_isolated_hook_chain_and_redacts_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_cli = root / "fake-codex"
            fake_cli.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, shlex, subprocess, sys\n"
                "args = sys.argv[1:]\n"
                "assert '--ephemeral' in args and '--json' in args\n"
                "assert args[args.index('--sandbox') + 1] == 'read-only'\n"
                "assert args[args.index('--model') + 1] == 'offline-model'\n"
                "config_path = os.path.join(os.environ['CODEX_HOME'], 'hooks.json')\n"
                "config = json.load(open(config_path, encoding='utf-8'))\n"
                "handler = config['hooks']['UserPromptSubmit'][0]['hooks'][0]\n"
                "hook = subprocess.run(shlex.split(handler['command']), input=json.dumps({"
                "'hook_event_name': 'UserPromptSubmit', 'prompt': 'private synthetic prompt'}), "
                "text=True, capture_output=True, check=True)\n"
                "context = json.loads(hook.stdout)['hookSpecificOutput']['additionalContext']\n"
                "token = context.rsplit(' ', 1)[-1]\n"
                "print(json.dumps({'type':'item.completed','item':{'type':'agent_message',"
                "'text':token + ' I will read the fixture.'}}))\n"
                "print(json.dumps({'type':'item.started','item':{'type':'command_execution',"
                "'name':'exec_command'}}))\n"
                "print(json.dumps({'type':'turn.completed'}))\n"
                "",
                encoding="utf-8",
            )
            fake_cli.chmod(0o700)
            auth = root / "auth.json"
            auth.write_text('{"access_token":"SYNTHETIC_SECRET_MUST_NOT_LEAK"}', encoding="utf-8")

            result = probe.run_probe(
                model="offline-model", codex=str(fake_cli), timeout=5, auth_path=auth,
            )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["assistant_observed"])
        self.assertTrue(result["tool_observed"])
        self.assertTrue(result["canary_before_first_tool"])
        rendered = json.dumps(result)
        self.assertNotIn("SYNTHETIC_SECRET", rendered)
        self.assertNotIn("private synthetic prompt", rendered)
        self.assertNotIn("I will read the fixture", rendered)

    def test_fake_cli_exercises_isolated_subagent_hook_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_cli = root / "fake-codex"
            fake_cli.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, shlex, subprocess, sys\n"
                "args = sys.argv[1:]\n"
                "assert '--ephemeral' in args and '--json' in args and '--enable' in args\n"
                "assert args[args.index('--enable') + 1] == 'multi_agent'\n"
                "assert args[args.index('--sandbox') + 1] == 'read-only'\n"
                "assert args[args.index('--model') + 1] == 'offline-model'\n"
                "config_path = os.path.join(os.environ['CODEX_HOME'], 'hooks.json')\n"
                "config = json.load(open(config_path, encoding='utf-8'))\n"
                "handler = config['hooks']['SubagentStart'][0]['hooks'][0]\n"
                "event = {'hook_event_name':'SubagentStart','agent_type':'explorer',"
                "'agent_id':'synthetic-child'}\n"
                "hook = subprocess.run(shlex.split(handler['command']), input=json.dumps(event), "
                "text=True, capture_output=True, check=True)\n"
                "context = json.loads(hook.stdout)['hookSpecificOutput']['additionalContext']\n"
                "token = context.rsplit(' ', 1)[-1]\n"
                "print(json.dumps({'type':'item.completed','agent_type':'explorer',"
                "'item':{'type':'agent_message','text':token + ' child response'}}))\n"
                "print(json.dumps({'type':'item.started','agent_type':'explorer',"
                "'item':{'type':'command_execution','name':'exec_command'}}))\n"
                "print(json.dumps({'type':'turn.completed'}))\n",
                encoding="utf-8",
            )
            fake_cli.chmod(0o700)
            auth = root / "auth.json"
            auth.write_text('{"access_token":"SYNTHETIC_SECRET_MUST_NOT_LEAK"}', encoding="utf-8")

            result = probe.run_subagent_probe(
                model="offline-model", codex=str(fake_cli), timeout=5, auth_path=auth,
            )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["child_observed"])
        self.assertTrue(result["canary_before_first_tool"])
        rendered = json.dumps(result)
        self.assertNotIn("SYNTHETIC_SECRET", rendered)
        self.assertNotIn("child response", rendered)

    def test_hook_config_and_auth_copy_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / ".codex"
            probe._write_hook_config(profile, "a1b2c3d4e5f60718293a4b5c")
            config_path = profile / "hooks.json"
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            handler = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]
            self.assertEqual(handler["additionalContextLimit"], 400)
            self.assertIn("--hook-canary", handler["command"])
            probe._write_subagent_hook_config(profile, "a1b2c3d4e5f60718293a4b5c", root / "receipt")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            handler = config["hooks"]["SubagentStart"][0]["hooks"][0]
            self.assertEqual(config["hooks"]["SubagentStart"][0]["matcher"], "^explorer$")
            self.assertEqual(handler["additionalContextLimit"], 400)
            self.assertIn("--subagent-hook-canary", handler["command"])
            source = root / "source-auth.json"
            source.write_text('{"access_token":"synthetic"}', encoding="utf-8")
            target = profile / "auth.json"
            self.assertTrue(probe._copy_auth(source, target))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_model_is_required_and_timeout_is_bounded(self):
        with self.assertRaises(ValueError):
            probe.run_probe(model=" ")
        with self.assertRaises(ValueError):
            probe.run_probe(model="offline-model", timeout=probe.MAX_TIMEOUT + 1)
        with self.assertRaises(ValueError):
            probe.run_subagent_probe(model="offline-model", timeout=probe.MAX_TIMEOUT + 1)

    def test_event_collector_enforces_output_cap(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "print('x' * 80)"], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        with mock.patch.object(probe, "MAX_OUTPUT_BYTES", 16):
            lines, failure = probe._collect_events(process, timeout=3)
        self.assertEqual(lines, [])
        self.assertEqual(failure, "output_limit")
        self.assertIsNotNone(process.returncode)


if __name__ == "__main__":
    unittest.main()
