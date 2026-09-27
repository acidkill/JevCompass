"""Offline checks for the prospective local plan comparison."""
import importlib.util
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "plan_pair_tests", ROOT / "scripts/pilot_plan_quality_pair.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class PlanPairTests(unittest.TestCase):
    def execute(self, root, mutate=False, missing=False):
        calls = []

        def arm(**kwargs):
            calls.append(kwargs)
            if mutate:
                (kwargs["fixture"] / "INVOICE_CONTRACT.md").write_text("changed")
            return {
                "status": "failed" if missing else "completed",
                "failure": "plan_incomplete" if missing else None,
                "plan_elapsed_ms": 20,
                "token_usage": None,
                "billing_cost_usd": None,
            }, None if missing else "A synthetic conditional plan."

        with mock.patch.object(runner.core, "_copy_auth", return_value=True), \
             mock.patch.object(runner, "run_arm", side_effect=arm), \
             mock.patch("jevcompass.strategy.DecisionsClient", side_effect=AssertionError("remote")):
            receipt = runner.run_pair(output_dir=root / "output", model="gpt-6-luna")
        return receipt, calls

    def test_equal_evidence_private_artifacts_and_no_remote(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, calls = self.execute(root)
            self.assertEqual(receipt["status"], "pending_manual_assessment")
            self.assertEqual(receipt["remote_invocations"], 0)
            self.assertEqual(receipt["coding_completion_status"], "not_evaluated")
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(call["prompt"].startswith(runner.PROMPT) for call in calls))
            self.assertEqual(sum("Optional strategy:" in call["prompt"] for call in calls), 1)
            serialized = (root / "output/receipt.json").read_text()
            self.assertNotIn("A synthetic conditional plan", serialized)
            self.assertNotIn(str(root), serialized)
            for path in (root / "output").iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(receipt["blind_assessment_status"], "pending")
            self.assertTrue(all(x["token_usage"] is None for x in receipt["arms"].values()))

    def test_mutation_invalidates_plan_and_cannot_receive_quality_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, _ = self.execute(root, mutate=True)
            self.assertEqual(receipt["status"], "failed")
            self.assertFalse(list((root / "output").glob("*-plan.md")))
            for arm in receipt["arms"].values():
                self.assertEqual(arm["failure"], "fixture_mutated")
                self.assertIsNone(arm["plan_sha256"])

    def test_missing_plan_and_existing_output_are_not_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, calls = self.execute(root, missing=True)
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(len(calls), 2)
            with self.assertRaises(FileExistsError):
                self.execute(root)

    def test_preregistered_digest_mismatch_stops_before_agent_or_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            with mock.patch.object(runner, "run_arm") as arm:
                with self.assertRaisesRegex(ValueError, "digest mismatch"):
                    runner.run_pair(output_dir=output, model="gpt-6-luna",
                                    expected_fixture_sha256="0" * 64)
            arm.assert_not_called()
            self.assertFalse(output.exists())

    def test_cli_process_uses_read_only_and_redacts_stream(self):
        events = [
            json.dumps({"type": "item.completed", "item": {
                "type": "agent_message", "text": "Private synthetic plan"}}),
            json.dumps({"type": "turn.completed"}),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(runner.subprocess, "Popen") as popen, \
                 mock.patch.object(runner.core, "_collect_events",
                                   return_value=(events, [1, 2], None)):
                result, plan = runner.run_arm(
                    codex="codex", model="gpt-6-luna", effort="low",
                    timeout=10, max_tokens=1000, fixture=root, home=root,
                    prompt=runner.PROMPT)
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
            self.assertIn("--ignore-user-config", command)
            self.assertNotIn("OPENROUTER_API_KEY", popen.call_args.kwargs["env"])
            self.assertEqual(plan, "Private synthetic plan")
            self.assertNotIn(plan, json.dumps(result))
            self.assertIsNone(result["token_usage"])


if __name__ == "__main__":
    unittest.main()
