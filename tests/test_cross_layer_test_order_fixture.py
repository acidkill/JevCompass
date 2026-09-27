"""Freeze the cross-layer case and verify distinct defects in disposable copies."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cross_layer_test_order"
UNIT = "test_unit*.py"
INTEGRATION = "test_integration*.py"
FULL = "test*.py"
UNIT_COMMAND = "python -m unittest discover -s tests -p 'test_unit*.py' -v"
INTEGRATION_COMMAND = "python -m unittest discover -s tests -p 'test_integration*.py' -v"
FULL_COMMAND = "python -m unittest discover -s tests -v"


def run_suite(root: Path, pattern: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", pattern, "-v"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )


def copy_fixture(parent: Path) -> Path:
    root = parent / "fixture"
    shutil.copytree(FIXTURE, root)
    return root


def correct_seeded_behavior(root: Path) -> None:
    source = root / "checkout" / "service.py"
    contents = source.read_text(encoding="utf-8")
    missing_fee = "return 0  # Seeded implementation is missing the newly specified fee."
    contents = contents.replace(
        missing_fee,
        "return (subtotal_cents * 2 + 50) // 100",
        1,
    )
    missing_field = (
        '        "subtotal_cents": quote.subtotal_cents,\n'
        '        "total_cents": quote.total_cents,'
    )
    contents = contents.replace(
        missing_field,
        '        "subtotal_cents": quote.subtotal_cents,\n'
        '        "service_fee_cents": quote.service_fee_cents,\n'
        '        "total_cents": quote.total_cents,',
        1,
    )
    if missing_fee in contents or missing_field in contents:
        raise AssertionError("reference-repair-anchor-missing")
    source.write_text(contents, encoding="utf-8")


class CrossLayerTestOrderFixtureTests(unittest.TestCase):
    def test_seeded_behavior_fails_distinct_focused_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = copy_fixture(Path(directory))
            unit = run_suite(root, UNIT)
            integration = run_suite(root, INTEGRATION)
            self.assertEqual(unit.returncode, 1, unit.stderr)
            self.assertIn("test_half_cent_rounds_half_up", unit.stderr)
            self.assertEqual(integration.returncode, 1, integration.stderr)
            self.assertIn("test_success_json_contract", integration.stderr)

    def test_rounding_mutant_fails_unit_but_not_typical_json_integration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = copy_fixture(Path(directory))
            correct_seeded_behavior(root)
            source = root / "checkout" / "service.py"
            contents = source.read_text(encoding="utf-8")
            exact = "return (subtotal_cents * 2 + 50) // 100"
            self.assertIn(exact, contents)
            source.write_text(
                contents.replace(exact, "return round(subtotal_cents * 0.02)", 1),
                encoding="utf-8",
            )
            unit = run_suite(root, UNIT)
            integration = run_suite(root, INTEGRATION)
            self.assertEqual(unit.returncode, 1, unit.stderr)
            self.assertIn("test_half_cent_rounds_half_up", unit.stderr)
            self.assertEqual(integration.returncode, 0, integration.stderr)

    def test_json_mapping_mutant_fails_integration_but_not_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = copy_fixture(Path(directory))
            correct_seeded_behavior(root)
            source = root / "checkout" / "service.py"
            contents = source.read_text(encoding="utf-8")
            mapping = '"service_fee_cents": quote.service_fee_cents,'
            self.assertIn(mapping, contents)
            source.write_text(
                contents.replace(mapping, '"service_fee_cents": quote.subtotal_cents,', 1),
                encoding="utf-8",
            )
            unit = run_suite(root, UNIT)
            integration = run_suite(root, INTEGRATION)
            self.assertEqual(unit.returncode, 0, unit.stderr)
            self.assertEqual(integration.returncode, 1, integration.stderr)
            self.assertIn("test_success_json_contract", integration.stderr)

    def test_reference_repair_passes_mandatory_full_suite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = copy_fixture(Path(directory))
            correct_seeded_behavior(root)
            full = run_suite(root, FULL)
            self.assertEqual(full.returncode, 0, full.stderr)
            self.assertIn("Ran 5 tests", full.stderr)

    def test_candidate_metadata_matches_visible_commands_and_keeps_full_gate(self):
        options = json.loads((FIXTURE / "test-options.json").read_text(encoding="utf-8"))
        self.assertEqual(options["surface"], "mixed")
        self.assertEqual(
            {item["kind"] for item in options["candidates"]},
            {"unit", "integration"},
        )
        self.assertEqual(
            {item["coverage"] for item in options["candidates"]},
            {"direct"},
        )
        self.assertEqual([item["runtime"] for item in options["candidates"]], ["fast", "slow"])
        self.assertEqual(
            [item["command"] for item in options["required"]],
            [FULL_COMMAND],
        )
        visible = (FIXTURE / "TESTING.md").read_text(encoding="utf-8")
        for item in (*options["candidates"], *options["required"]):
            self.assertIn(item["command"], visible)


if __name__ == "__main__":
    unittest.main()
