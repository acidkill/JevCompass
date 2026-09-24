"""Offline checks for the temporary synthetic CLI core case fixture."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "cli_core"
EXPECTED = ROOT / "tests" / "evaluation" / "cli_core_expected.md"


@pytest.fixture
def cli_project(tmp_path: Path) -> Path:
    project = tmp_path / "cli-core-project"
    shutil.copytree(FIXTURE, project)
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=fixture-main", str(project)],
        check=True,
        capture_output=True,
        text=True,
    )
    return project


def test_fixture_contains_inputs_for_each_substantive_case(cli_project: Path):
    assert (cli_project / "tinytext" / "text.py").is_file()
    assert (cli_project / "tests" / "test_text.py").is_file()
    assert (cli_project / "scripts" / "render_report.sh").is_file()
    assert (cli_project / "API_REQUIREMENTS.md").is_file()

    help_text = subprocess.run(
        [sys.executable, "-m", "tinytext", "--help"],
        cwd=cli_project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "--check" in help_text
    assert "text file to normalize" in help_text
    assert "python -m unittest discover -s tests" in (cli_project / "README.md").read_text()

    syntax = subprocess.run(
        ["bash", "-n", "scripts/render_report.sh"],
        cwd=cli_project,
        capture_output=True,
        text=True,
    )
    assert syntax.returncode == 0, syntax.stderr
    assert "OUTPUT_PATH" in (cli_project / "scripts" / "render_report.sh").read_text()


def test_fixture_supports_all_six_routine_controls(cli_project: Path):
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=cli_project, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    assert branch == "fixture-main"

    top_level_files = sorted(path.name for path in cli_project.iterdir() if path.is_file())
    assert top_level_files == ["AGENTS.md", "API_REQUIREMENTS.md", "README.md", "pyproject.toml"]
    assert (cli_project / "README.md").is_file()

    project_file_size = subprocess.run(
        ["wc", "-c", "pyproject.toml"], cwd=cli_project, check=True,
        capture_output=True, text=True,
    ).stdout.split()[0]
    assert int(project_file_size) == (cli_project / "pyproject.toml").stat().st_size

    python_files = sorted(
        path.relative_to(cli_project).as_posix()
        for path in cli_project.glob("*/*.py")
    ) + sorted(
        path.relative_to(cli_project).as_posix()
        for path in cli_project.glob("*/*/*.py")
    )
    assert python_files == [
        "tests/test_text.py", "tinytext/__init__.py", "tinytext/__main__.py",
        "tinytext/cli.py", "tinytext/text.py"
    ]
    assert "timeout" in (cli_project / "README.md").read_text()


def test_expected_checklist_covers_the_assigned_cases():
    checklist = EXPECTED.read_text(encoding="utf-8")
    for case in ("P01", "P03", "P05", "P07", "R01", "R02", "R03", "R04", "R05", "R06"):
        assert f"| {case} |" in checklist
    assert "do not imply any Codex arm" in checklist
