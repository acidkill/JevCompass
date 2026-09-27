"""Integration guards for the staged synthetic query migration case."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/query_migration_triage"
PROFILE_PATH = ROOT / "tests/fixtures/query_migration_triage.case.json"
ORACLE = ROOT / "scripts/pilot_oracles/query_migration.py"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import pilot_contract_triage_pair as pair  # noqa: E402


def _run_oracle(fixture: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, str(ORACLE), "--fixture-dir", str(fixture)],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False,
    )


class QueryMigrationFixtureProfileTests(unittest.TestCase):
    def test_profile_loads_and_keeps_diagnostic_candidates_separate_from_causal_order(self) -> None:
        profile = pair.load_case_profile(PROFILE_PATH)
        self.assertEqual(profile.case_id, "query_migration_triage")
        self.assertEqual(profile.fixture_source, FIXTURE)
        self.assertEqual(profile.source_file, "query_encoder.py")
        self.assertEqual(profile.focused_test_file, "tests/test_query_encoder.py")
        self.assertEqual(profile.oracle_script, ORACLE)
        self.assertEqual(
            set(profile.triage_hypotheses),
            {"assertion_behavior_regression", "assertion_expectation_drift"},
        )
        self.assertEqual(
            set(profile.triage_accepted_ids),
            {*profile.triage_hypotheses, "confirm_behavior_contract"},
        )
        # Current runner sends only causal hypotheses; confirm remains accepted
        # metadata but is not claimed as a requested diagnostic until the argv
        # schema is split from causal ranking.
        argv = pair._triage_argv(1, profile)
        self.assertIn("assertion_behavior_regression", argv)
        self.assertIn("assertion_expectation_drift", argv)
        self.assertIn("confirm_behavior_contract", argv)
        self.assertNotIn("confirm_behavior_contract", profile.triage_hypotheses)
        self.assertTrue(profile.rank_hypotheses)
        self.assertEqual(profile.triage_accepted_statuses, ("no-remote-choice",))

    def test_frozen_seed_has_the_expected_focused_failure(self) -> None:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests",
             "-p", "test_query_encoder.py", "-v"],
            cwd=FIXTURE, env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL: test_v2_space_encoding", result.stderr)
        self.assertIn("q=blue%20sky", result.stderr)
        self.assertIn("Ran 3 tests", result.stderr)

    def test_oracle_accepts_source_repair_and_git_metadata_but_rejects_other_changes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="query-migration-test-") as temp:
            root = Path(temp)
            repaired = root / "repaired"
            shutil.copytree(FIXTURE, repaired)
            source = repaired / "query_encoder.py"
            text = source.read_text(encoding="utf-8")
            self.assertIn("return urlencode(pairs, quote_via=quote)", text)
            source.write_text(
                text.replace("from urllib.parse import quote, urlencode",
                             "from urllib.parse import urlencode")
                    .replace("return urlencode(pairs, quote_via=quote)",
                             "return urlencode(pairs)"),
                encoding="utf-8",
            )
            (repaired / ".git").mkdir()
            (repaired / ".git/HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            accepted = _run_oracle(repaired)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertIn("focused/full tests passed", accepted.stdout)
            self.assertIn("diff check passed", accepted.stdout)

            tampered = root / "tampered"
            shutil.copytree(repaired, tampered)
            readme = tampered / "README.md"
            readme.write_text(readme.read_text(encoding="utf-8") + "\nchanged\n",
                              encoding="utf-8")
            rejected = _run_oracle(tampered)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("immutable_fixture_file_changed:README.md", rejected.stderr)

            extra = root / "extra"
            shutil.copytree(repaired, extra)
            (extra / "unexpected.txt").write_text("not part of the fixture\n", encoding="utf-8")
            rejected_extra = _run_oracle(extra)
            self.assertNotEqual(rejected_extra.returncode, 0)
            self.assertIn("fixture_file_set_changed", rejected_extra.stderr)


if __name__ == "__main__":
    unittest.main()
