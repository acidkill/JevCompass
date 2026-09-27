"""Offline guards for the synthetic profile-cache triage fixture."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/ambiguous_cache_triage"
PROFILE_PATH = ROOT / "tests/fixtures/ambiguous_cache_triage.case.json"
ORACLE = ROOT / "scripts/pilot_oracles/ambiguous_cache.py"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import pilot_contract_triage_pair as pair  # noqa: E402


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=15,
        check=False,
    )


def _run_oracle(fixture: Path) -> subprocess.CompletedProcess[str]:
    return _run([sys.executable, str(ORACLE), "--fixture-dir", str(fixture)], cwd=ROOT)


class AmbiguousCacheFixtureTests(unittest.TestCase):
    def test_case_profile_loads_with_two_causal_candidates_and_separate_diagnostic(self):
        profile = pair.load_case_profile(PROFILE_PATH)
        self.assertEqual(profile.case_id, "ambiguous_cache_triage")
        self.assertEqual(profile.fixture_source, FIXTURE)
        self.assertEqual(profile.source_file, "profile_cache.py")
        self.assertEqual(profile.focused_test_file, "tests/test_profile_service.py")
        self.assertEqual(profile.oracle_script, ORACLE)
        self.assertEqual(
            set(profile.triage_hypotheses),
            {"assertion_behavior_regression", "assertion_expectation_drift"},
        )
        self.assertIn("confirm_behavior_contract", profile.triage_accepted_ids)
        self.assertEqual(
            profile.triage_observations,
            {"assertion": ("legacy_fixture_conflict",)},
        )

    def test_seed_fails_focused_and_full_with_the_expected_behavioral_failure(self):
        profile = pair.load_case_profile(PROFILE_PATH)
        focused = _run(list(profile.focused_command), cwd=FIXTURE)
        self.assertEqual(focused.returncode, 1, focused.stdout)
        self.assertIn("FAIL: test_identical_profile_ids_are_isolated_between_tenants", focused.stdout)
        self.assertIn("AssertionError", focused.stdout)
        self.assertIn("Ran 3 tests", focused.stdout)

        full = _run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=FIXTURE,
        )
        self.assertEqual(full.returncode, 1, full.stdout)
        self.assertIn("FAIL: test_identical_profile_ids_are_isolated_between_tenants", full.stdout)
        self.assertIn("Ran 3 tests", full.stdout)

    def test_independent_oracle_accepts_repair_and_rejects_tampered_source_or_evidence(self):
        with tempfile.TemporaryDirectory(prefix="ambiguous-cache-test-") as temp:
            root = Path(temp)
            repaired = root / "repaired"
            shutil.copytree(FIXTURE, repaired)
            source = repaired / "profile_cache.py"
            seeded = source.read_text(encoding="utf-8")
            self.assertIn("return profile_id.casefold()", seeded)
            source.write_text(
                seeded.replace(
                    "return profile_id.casefold()",
                    "return (tenant_id, profile_id.casefold())",
                    1,
                ),
                encoding="utf-8",
            )
            accepted = _run_oracle(repaired)
            self.assertEqual(accepted.returncode, 0, accepted.stdout)
            self.assertIn("oracle_passed", accepted.stdout)

            tampered = root / "tampered"
            shutil.copytree(repaired, tampered)
            changed = tampered / "profile_cache.py"
            text = changed.read_text(encoding="utf-8")
            changed.write_text(
                text.replace(
                    "return (tenant_id, profile_id.casefold())",
                    "return profile_id.casefold()",
                    1,
                ),
                encoding="utf-8",
            )
            rejected = _run_oracle(tampered)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("oracle_mismatch:tenant_cache_isolation", rejected.stdout)

            evidence_tamper = root / "evidence-tamper"
            shutil.copytree(repaired, evidence_tamper)
            changelog = evidence_tamper / "CHANGELOG.md"
            changelog.write_text(
                changelog.read_text(encoding="utf-8") + "\nEdited evidence.\n",
                encoding="utf-8",
            )
            rejected_evidence = _run_oracle(evidence_tamper)
            self.assertNotEqual(rejected_evidence.returncode, 0)
            self.assertIn("immutable_fixture_file_changed:CHANGELOG.md", rejected_evidence.stdout)


if __name__ == "__main__":
    unittest.main()
