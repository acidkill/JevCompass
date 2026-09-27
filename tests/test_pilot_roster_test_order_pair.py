"""Offline fixture, independent-oracle, and privacy tests for roster pairing."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pilot_roster_test_order_pair.py"
FIXTURE = ROOT / "tests" / "fixtures" / "roster_test_order"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("pilot_roster_test_order_pair", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)

UNIT = ["python", "-m", "unittest", "discover", "-s", "tests",
        "-p", "test_unit*.py", "-v"]
INTEGRATION = ["python", "-m", "unittest", "discover", "-s", "tests",
               "-p", "test_integration*.py", "-v"]
FULL = ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
REFERENCE_SERVICE = '''"""Roster domain transform and local JSON command-line adapter."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Any


@dataclass(frozen=True)
class Person:
    email: str
    name: str
    team: str


def import_roster(records: list[dict[str, Any]]) -> list[Person]:
    people: list[Person] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("each roster entry must be an object")
        values = {}
        for key in ("email", "name", "team"):
            value = record.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a non-empty string")
            values[key] = value.strip()
        email = values["email"].lower()
        if email in seen:
            continue
        seen.add(email)
        people.append(Person(email=email, name=values["name"], team=values["team"]))
    return people


def _json_payload(people: list[Person]) -> dict[str, Any]:
    return {"count": len(people), "roster": [asdict(person) for person in people]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a roster JSON file")
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("input must be a JSON array")
        people = import_roster(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"roster input error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(_json_payload(people), separators=(",", ":"), sort_keys=True))
    return 0
'''


def run_in(fixture: Path, command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, cwd=fixture, capture_output=True, text=True,
        timeout=30, check=False,
    )


def copy_fixture(destination: Path) -> Path:
    target = destination / "fixture"
    shutil.copytree(FIXTURE, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return target


class RosterPairFixtureTests(unittest.TestCase):
    def test_exact_fixture_is_pinned_and_source_is_the_only_editable_file(self):
        self.assertTrue(runner.verify_fixture())
        hashes = runner._fixture_hashes()
        self.assertIsNotNone(hashes)
        self.assertIn("roster/service.py", hashes)
        self.assertIn("tests/frozen_oracle.py", hashes)
        self.assertIn("test-options.json", hashes)
        self.assertEqual(runner.CHANGED_FILE, "roster/service.py")

    def test_modified_contract_or_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            copy = copy_fixture(Path(directory))
            options = copy / "test-options.json"
            options.write_text(options.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with mock.patch.object(runner, "FIXTURE", copy), mock.patch.object(
                runner, "MANIFEST", copy / "fixture-manifest.json"
            ):
                self.assertFalse(runner.verify_fixture())

    def test_seeded_failures_are_identified_by_the_frozen_tests_and_oracle(self):
        unit = run_in(FIXTURE, UNIT)
        integration = run_in(FIXTURE, INTEGRATION)
        oracle = run_in(FIXTURE, ["python", "tests/frozen_oracle.py"])
        self.assertEqual(unit.returncode, 1)
        self.assertIn("FAIL: test_import_deduplicates_casefolded_email_preserving_first_seen", unit.stderr + unit.stdout)
        self.assertEqual(integration.returncode, 1)
        self.assertIn("FAIL: test_cli_emits_roster_json_contract", integration.stderr + integration.stdout)
        self.assertEqual(oracle.returncode, 1)
        self.assertEqual(run_in(FIXTURE, FULL).returncode, 1)

    def test_adapter_repair_does_not_cover_domain_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = copy_fixture(Path(directory))
            source = (fixture / "roster/service.py").read_text(encoding="utf-8")
            source = source.replace(
                'return {"count": len(people), "people": [asdict(person) for person in people]}',
                'return {"count": len(people), "roster": [asdict(person) for person in people]}',
            )
            (fixture / "roster/service.py").write_text(source, encoding="utf-8")
            unit = run_in(fixture, UNIT)
            integration = run_in(fixture, INTEGRATION)
            oracle = run_in(fixture, ["python", "tests/frozen_oracle.py"])
            self.assertEqual(unit.returncode, 1)
            self.assertEqual(integration.returncode, 0)
            self.assertEqual(oracle.returncode, 1)

    def test_domain_mutant_passes_unique_input_integration_but_fails_oracle(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = copy_fixture(Path(directory))
            (fixture / "roster/service.py").write_text(REFERENCE_SERVICE, encoding="utf-8")
            source = (fixture / "roster/service.py").read_text(encoding="utf-8")
            source = source.replace(
                "        if email in seen:\n            continue\n        seen.add(email)\n",
                "",
            )
            self.assertNotEqual(source, REFERENCE_SERVICE)
            (fixture / "roster/service.py").write_text(source, encoding="utf-8")
            unit = run_in(fixture, UNIT)
            integration = run_in(fixture, INTEGRATION)
            oracle = run_in(fixture, ["python", "tests/frozen_oracle.py"])
            self.assertEqual(unit.returncode, 1)
            self.assertEqual(integration.returncode, 0)
            self.assertEqual(oracle.returncode, 1)

    def test_frozen_oracle_catches_order_first_record_and_invalid_type_mutants(self):
        order_mutant = REFERENCE_SERVICE.replace(
            "    return people\n",
            "    return sorted(people, key=lambda person: person.email)\n",
        )
        last_wins_mutant = REFERENCE_SERVICE.replace(
            "        if email in seen:\n            continue\n",
            "        if email in seen:\n"
            "            people = [Person(email=email, name=values['name'], team=values['team']) "
            "if item.email == email else item for item in people]\n"
            "            continue\n",
        )
        permissive_types_mutant = REFERENCE_SERVICE.replace(
            "            if not isinstance(value, str) or not value.strip():",
            "            if False:",
        )
        mutants = (order_mutant, last_wins_mutant, permissive_types_mutant)
        for index, mutant in enumerate(mutants):
            with self.subTest(mutant=index):
                self.assertNotEqual(mutant, REFERENCE_SERVICE)
                with tempfile.TemporaryDirectory() as directory:
                    fixture = copy_fixture(Path(directory))
                    (fixture / "roster/service.py").write_text(mutant, encoding="utf-8")
                    self.assertEqual(
                        run_in(fixture, ["python", "tests/frozen_oracle.py"]).returncode,
                        1,
                    )

    def test_full_reference_repair_passes_both_candidates_full_suite_and_oracle(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = copy_fixture(Path(directory))
            (fixture / "roster/service.py").write_text(REFERENCE_SERVICE, encoding="utf-8")
            for command in (UNIT, INTEGRATION, FULL, ["python", "tests/frozen_oracle.py"]):
                with self.subTest(command=command):
                    result = run_in(fixture, command)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_uses_frozen_profile_and_does_not_mutate_other_runner(self):
        baseline = runner.runner.FIXTURE
        baseline_engine_fixture = runner.runner.engine.FIXTURE
        observed = {}

        def fake_pair(**kwargs):
            observed.update(kwargs)
            return {"status": "completed", "arms": {}}

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(runner.runner, "run_pair", fake_pair), \
             mock.patch.object(runner.runner.engine.shutil, "which", return_value="/usr/bin/codex"), \
             contextlib.redirect_stdout(output):
            code = runner.main([
                "--live", "--model", "offline-test-model", "--reasoning-effort", "low",
                "--timeout", "30", "--seed", "1", "--output-dir", directory,
            ])
        self.assertEqual(code, 0)
        self.assertEqual(observed["baseline_first_candidate_policy"], "either")
        self.assertEqual(observed["advice_policy"], "nonbinding")
        self.assertEqual(Path(observed["fixture_source"]), FIXTURE)
        self.assertEqual(runner.runner.FIXTURE, baseline)
        self.assertEqual(runner.runner.engine.FIXTURE, baseline_engine_fixture)
        cross_spec = importlib.util.spec_from_file_location(
            "pilot_cross_layer_isolation_probe",
            ROOT / "scripts" / "pilot_cross_layer_test_order_pair.py",
        )
        cross_module = importlib.util.module_from_spec(cross_spec)
        assert cross_spec and cross_spec.loader
        cross_spec.loader.exec_module(cross_module)
        self.assertEqual(cross_module.FIXTURE.name, "cross_layer_test_order")
        self.assertEqual(cross_module.engine.FIXTURE, cross_module.FIXTURE)
        self.assertEqual(json.loads(output.getvalue())["status"], "completed")


if __name__ == "__main__":
    unittest.main()
