"""Frozen black-box oracle: checks only CLI behavior, never imports service code."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile


def run_cli(value):
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "input.json"
        source.write_text(json.dumps(value), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-m", "roster", "--input", str(source)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    return completed


def main():
    duplicate_case = run_cli([
        {"email": " B@Example.test ", "name": "First B", "team": " One "},
        {"email": "a@example.test", "name": "A", "team": "Two"},
        {"email": "b@example.test", "name": "Later B", "team": "Three"},
    ])
    expected = {
        "count": 2,
        "roster": [
            {"email": "b@example.test", "name": "First B", "team": "One"},
            {"email": "a@example.test", "name": "A", "team": "Two"},
        ],
    }
    if duplicate_case.returncode != 0 or duplicate_case.stderr or json.loads(duplicate_case.stdout) != expected:
        return 1
    empty = run_cli([])
    if empty.returncode != 0 or empty.stderr or json.loads(empty.stdout) != {"count": 0, "roster": []}:
        return 1
    for invalid in (
        [{"email": 7, "name": "Bad", "team": "One"}],
        [{"name": "Missing email", "team": "One"}],
        {"email": "not-an-array"},
    ):
        rejected = run_cli(invalid)
        if rejected.returncode != 2 or rejected.stdout:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
