"""Offline checks for the temporary synthetic CLI core case fixture."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "cli_core"
EXPECTED = ROOT / "tests" / "evaluation" / "cli_core_expected.md"


class TestCliCoreFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.cli_project = Path(self._temporary_directory.name) / "cli-core-project"
        shutil.copytree(FIXTURE, self.cli_project)
        subprocess.run(
            ["git", "init", "--quiet", "--initial-branch=fixture-main", str(self.cli_project)],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_fixture_contains_inputs_for_each_substantive_case(self) -> None:
        self.assertTrue((self.cli_project / "tinytext" / "text.py").is_file())
        self.assertTrue((self.cli_project / "tests" / "test_text.py").is_file())
        self.assertTrue((self.cli_project / "scripts" / "render_report.sh").is_file())
        self.assertTrue((self.cli_project / "API_REQUIREMENTS.md").is_file())

        help_text = subprocess.run(
            [sys.executable, "-m", "tinytext", "--help"],
            cwd=self.cli_project,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("--check", help_text)
        self.assertIn("text file to normalize", help_text)
        self.assertIn(
            "python -m unittest discover -s tests",
            (self.cli_project / "README.md").read_text(),
        )

        syntax = subprocess.run(
            ["bash", "-n", "scripts/render_report.sh"],
            cwd=self.cli_project,
            capture_output=True,
            text=True,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertIn(
            "OUTPUT_PATH",
            (self.cli_project / "scripts" / "render_report.sh").read_text(),
        )

    def test_fixture_supports_all_six_routine_controls(self) -> None:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=self.cli_project,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(branch, "fixture-main")

        top_level_files = sorted(
            path.name for path in self.cli_project.iterdir() if path.is_file()
        )
        self.assertEqual(
            top_level_files,
            ["AGENTS.md", "API_REQUIREMENTS.md", "README.md", "pyproject.toml"],
        )
        self.assertTrue((self.cli_project / "README.md").is_file())

        project_file_size = subprocess.run(
            ["wc", "-c", "pyproject.toml"],
            cwd=self.cli_project,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()[0]
        self.assertEqual(
            int(project_file_size),
            (self.cli_project / "pyproject.toml").stat().st_size,
        )

        python_files = sorted(
            path.relative_to(self.cli_project).as_posix()
            for path in self.cli_project.glob("*/*.py")
        ) + sorted(
            path.relative_to(self.cli_project).as_posix()
            for path in self.cli_project.glob("*/*/*.py")
        )
        self.assertEqual(
            python_files,
            [
                "tests/test_text.py",
                "tinytext/__init__.py",
                "tinytext/__main__.py",
                "tinytext/cli.py",
                "tinytext/text.py",
            ],
        )
        self.assertIn("timeout", (self.cli_project / "README.md").read_text())

    def test_expected_checklist_covers_the_assigned_cases(self) -> None:
        checklist = EXPECTED.read_text(encoding="utf-8")
        for case in (
            "P01", "P03", "P05", "P07", "R01", "R02", "R03", "R04", "R05", "R06"
        ):
            with self.subTest(case=case):
                self.assertIn(f"| {case} |", checklist)
        self.assertIn("do not imply any Codex arm", checklist)
