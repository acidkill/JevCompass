from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pilot_contract_triage_pair as runner


def _event(kind: str, event_id: str, argv: list[str], **extra: object) -> str:
    item = {
        "id": event_id,
        "type": "command_execution",
        "command": shlex.join(argv),
        **extra,
    }
    return json.dumps({"type": kind, "item": item})


def _repair_diff_receipt(check_argv: list[str] | None, exit_code: int | None):
    lines = []
    if check_argv is not None:
        lines = [
            _event("item.started", "diff", check_argv),
            _event("item.completed", "diff", check_argv, exit_code=exit_code),
        ]
    result, _ = runner._event_receipts(
        lines, [1.0, 2.0], 0.0,
        profile=types.SimpleNamespace(outcome_mode="repair", focused_command=(), evidence_files={}),
    )
    return result


class PilotProfileGitValidationTests(unittest.TestCase):
    def _profile(self, fixture: Path, oracle: Path) -> runner.CaseProfile:
        oracle.write_text("pass\n", encoding="utf-8")
        return runner.CaseProfile(
            case_id="synthetic-git-validation",
            fixture_source=fixture,
            task_prompt="Repair the copied source.",
            source_file="src/service.py",
            focused_test_file="tests/test_service.py",
            focused_command=("python", "-m", "unittest", "tests.test_service"),
            evidence_files={},
            evidence_markers={},
            failure_markers=("AssertionError",),
            triage_kinds=(),
            triage_hypotheses=(),
            triage_accepted_ids=(),
            triage_accepted_statuses=(),
            triage_observations={},
            rank_hypotheses=False,
            outcome_mode="repair",
            oracle_script=oracle,
            oracle_sha256=hashlib.sha256(oracle.read_bytes()).hexdigest(),
        )

    def test_repair_pair_aborts_before_agent_when_git_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = root / "fixture"
            (fixture / "src").mkdir(parents=True)
            (fixture / "tests").mkdir()
            (fixture / "src/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            (fixture / "tests/test_service.py").write_text("assert True\n", encoding="utf-8")
            profile = self._profile(fixture, root / "oracle.py")
            output = root / "output"
            with (
                patch.object(runner, "_verify_codex_version", return_value=True),
                patch.object(runner.core, "_copy_auth", return_value=True),
                patch.object(runner.shutil, "which", return_value=None),
                patch.object(runner, "_run_arm") as run_arm,
            ):
                receipt = runner.run_pair(
                    codex="codex", model="test-model", reasoning_effort="low",
                    timeout=2, seed=4, output_dir=output, case_profile=profile,
                )
            self.assertEqual(receipt["failure"], "repair_git_setup_failed")
            self.assertEqual(receipt["status"], "failed")
            run_arm.assert_not_called()
            self.assertEqual(
                json.loads((output / "receipt.json").read_text())["failure"],
                "repair_git_setup_failed",
            )

    def test_private_git_baseline_and_protected_digest_ignore_only_git_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp)
            (fixture / "src").mkdir()
            (fixture / "src/service.py").write_text("VALUE = 1\n", encoding="utf-8")
            before = runner._fixture_digest(
                fixture, exclude_relative="src/service.py", exclude_internal_git=True,
            )
            self.assertTrue(runner._initialize_private_git_baseline(fixture))
            after = runner._fixture_digest(
                fixture, exclude_relative="src/service.py", exclude_internal_git=True,
            )
            self.assertEqual(before, after)
            self.assertNotEqual(
                runner._fixture_digest(fixture, exclude_relative="src/service.py"),
                before,
            )

    def test_whitespace_patch_fails_real_diff_check_and_repair_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp)
            (fixture / "src").mkdir()
            source = fixture / "src/service.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")
            self.assertTrue(runner._initialize_private_git_baseline(fixture))
            source.write_text("VALUE = 2  \n", encoding="utf-8")
            check = subprocess.run(
                ["git", "diff", "--check"], cwd=fixture,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertNotEqual(check.returncode, 0)
            receipt, _ = runner._event_receipts(
                [
                    _event("item.started", "diff", ["git", "diff", "--check"]),
                    _event("item.completed", "diff", ["git", "diff", "--check"],
                           exit_code=check.returncode, aggregated_output=check.stderr.decode()),
                ],
                [1.0, 2.0], 0.0,
                profile=types.SimpleNamespace(outcome_mode="repair", focused_command=(), evidence_files={}),
            )
            self.assertTrue(receipt["agent_git_diff_check_invocation_observed"])
            self.assertEqual(receipt["agent_git_diff_check_exit_code"], check.returncode)
            self.assertFalse(receipt["agent_git_diff_check_passed"])
            self.assertFalse(runner._repair_git_diff_check_valid(receipt))

    def test_missing_or_nonzero_observed_diff_check_cannot_accept_repair(self):
        missing = _repair_diff_receipt(None, None)
        self.assertFalse(missing["agent_git_diff_check_invocation_observed"])
        self.assertEqual(missing["agent_git_diff_check_exit_codes"], [])
        self.assertFalse(runner._repair_git_diff_check_valid(missing))

        nonzero = _repair_diff_receipt(["git", "diff", "--check"], 2)
        self.assertTrue(nonzero["agent_git_diff_check_invocation_observed"])
        self.assertEqual(nonzero["agent_git_diff_check_exit_codes"], [2])
        self.assertFalse(nonzero["agent_git_diff_check_passed"])
        self.assertFalse(runner._repair_git_diff_check_valid(nonzero))

        accepted = _repair_diff_receipt(["git", "diff", "--check"], 0)
        self.assertTrue(runner._repair_git_diff_check_valid(accepted))


if __name__ == "__main__":
    unittest.main()
