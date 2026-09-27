"""Independent oracle and immutable-check verifier for the query migration fixture."""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

IMMUTABLE_SHA256 = {
    "CHANGELOG.md": "ccfb22eb8fb0c75773bea70f1f7d3566ec7d6c1f5743388664bd92655e21835d",
    "CONTRACT_v1.md": "2aa3178710bc90b6c073518e87652c760ff5357674b2f7153f605e0074215412",
    "CONTRACT_v2.md": "c10decdef269169115650ff34c76b1c1be3517756869466e7194561591af4d96",
    "README.md": "15cb774071942bc09b8d1fd32f88053a9d4be4fdeed71b207eb5b8d68c56d2cc",
    "legacy_query_golden.json": "e12b45986a7bace7f792aba8a49ca7e1749f7ec94e433a620daf90726695c870",
    "tests/test_query_encoder.py": "61e8874a8dbdc45e790ccf0a357fa7de812e235a2e8bfaeb6146efa98341da32",
}
BASELINE_SOURCE = '''"""Serialize ordered query pairs for the request client."""
from __future__ import annotations

from urllib.parse import quote, urlencode


def serialize_query(pairs: list[tuple[str, str]]) -> str:
    """Return query pairs in order, preserving the v1 quoting implementation."""
    return urlencode(pairs, quote_via=quote)
'''


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(
            f"independent_check_failed:{command[1:3]}:exit={result.returncode}:"
            f"{result.stderr[-1200:]}"
        )


parser = argparse.ArgumentParser()
parser.add_argument("--fixture-dir", required=True, type=Path)
args = parser.parse_args()
fixture = args.fixture_dir.resolve(strict=True)

# The only allowed source edit is query_encoder.py; all task/test evidence is frozen.
actual_paths = {
    path.relative_to(fixture).as_posix()
    for path in fixture.rglob("*")
    if path.is_file()
    and ".git" not in path.relative_to(fixture).parts
    and "__pycache__" not in path.relative_to(fixture).parts
}
expected_paths = set(IMMUTABLE_SHA256) | {"query_encoder.py"}
if actual_paths != expected_paths:
    raise SystemExit("fixture_file_set_changed")
for relative, expected in IMMUTABLE_SHA256.items():
    actual = hashlib.sha256((fixture / relative).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"immutable_fixture_file_changed:{relative}")

spec_source = fixture / "query_encoder.py"
# Independent behavior oracle.
spec_text = spec_source.read_text(encoding="utf-8")
namespace: dict[str, object] = {}
exec(compile(spec_text, str(spec_source), "exec"), namespace)
serialize_query = namespace["serialize_query"]
cases = [
    ([ ("q", "blue sky"), ("tag", "a+b") ], "q=blue+sky&tag=a%2Bb"),
    ([ ("city", "Ålesund café") ], "city=%C3%85lesund+caf%C3%A9"),
    ([ ("b", "2"), ("a", "") ], "b=2&a="),
]
for pairs, expected in cases:
    actual = serialize_query(pairs)  # type: ignore[operator]
    if actual != expected:
        raise SystemExit(f"oracle_mismatch:{expected!r}:{actual!r}")

# Re-run the exact focused and full fixture commands independently.
env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_query_encoder.py", "-v"], cwd=fixture, env=env)
run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=fixture, env=env)

# The copied arm has no .git directory. Build a throwaway baseline index so the
# exact candidate patch can still receive an independent `git diff --check`.
with tempfile.TemporaryDirectory(prefix="query-migration-diff-") as temp:
    work = Path(temp)
    candidate = work / "query_encoder.py"
    candidate.write_text(BASELINE_SOURCE, encoding="utf-8")
    run(["git", "init", "-q"], cwd=work)
    run(["git", "config", "user.name", "fixture-oracle"], cwd=work)
    run(["git", "config", "user.email", "fixture-oracle@invalid"], cwd=work)
    run(["git", "add", "query_encoder.py"], cwd=work)
    run(["git", "commit", "-q", "-m", "baseline"], cwd=work)
    candidate.write_bytes(spec_source.read_bytes())
    run(["git", "diff", "--check"], cwd=work)

print("oracle_passed:3 cases; focused/full tests passed; immutable files and diff check passed")
