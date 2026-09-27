"""Independent oracle and immutable-evidence verifier for the profile-cache fixture."""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

IMMUTABLE_SHA256 = {
    "CHANGELOG.md": "3e60ac0912115b4ddc52a52e1bc934f7116c0e6f773f373bf1a7215ebc5c2076",
    "CONTRACT_v1.md": "78a39fb809a7a6ff54f206b26994656ca53367fe0ce738bf08aee28267be1f20",
    "CONTRACT_v2.md": "4d0a421a2dbdc634d387bb70ad04b60e4ed2cca08a4cb295b8acce6880f0e970",
    "README.md": "77e8c7640879a71ef903d99926edac49a58c8863dc6b0fce1c52a0e13d9de14d",
    "legacy_profile_golden.json": "450be32462b8c33599052fcb2a3f77a94a103019a2db241fdfe7c63334c248d1",
    "profile_service.py": "1e470b789e634d084c55e5c0fc97e5eab746290ca5c4e734981c786d17cd9d77",
    "tests/test_profile_service.py": "f3c2652b81053cf0de5265749222b77dbc52e8a3330ad13e0a3378f6159e4c34",
}
BASELINE_SOURCE = '''"""Tiny in-memory cache used by the fictional profile service."""


def profile_cache_key(tenant_id: str, profile_id: str) -> object:
    """Return the cache key for a profile lookup."""
    return profile_id.casefold()


def cached_profile(cache: dict, tenant_id: str, profile_id: str, loader):
    key = profile_cache_key(tenant_id, profile_id)
    if key not in cache:
        cache[key] = loader(tenant_id, profile_id)
    return cache[key]
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

# Only profile_cache.py may change; task, tests, and consumer remain frozen.
actual_paths = {
    path.relative_to(fixture).as_posix()
    for path in fixture.rglob("*")
    if path.is_file()
    and ".git" not in path.relative_to(fixture).parts
    and "__pycache__" not in path.relative_to(fixture).parts
}
expected_paths = set(IMMUTABLE_SHA256) | {"profile_cache.py"}
if actual_paths != expected_paths:
    raise SystemExit("fixture_file_set_changed")
for relative, expected in IMMUTABLE_SHA256.items():
    actual = hashlib.sha256((fixture / relative).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"immutable_fixture_file_changed:{relative}")

sys.path.insert(0, str(fixture))
from profile_cache import cached_profile  # noqa: E402

# Independent behavior oracle: a normalized cache hit within one tenant, then
# an independent cache entry for the same profile identifier in another tenant.
calls: list[tuple[str, str]] = []


def load(tenant_id: str, profile_id: str) -> tuple[str, str]:
    calls.append((tenant_id, profile_id))
    return tenant_id, profile_id


cache: dict[object, tuple[str, str]] = {}
north_first = cached_profile(cache, "north", "USER-7", load)
north_hit = cached_profile(cache, "north", "user-7", load)
south_first = cached_profile(cache, "south", "USER-7", load)
if north_first != ("north", "USER-7") or north_hit is not north_first:
    raise SystemExit("oracle_mismatch:same_tenant_cache_hit")
if south_first != ("south", "USER-7"):
    raise SystemExit("oracle_mismatch:tenant_cache_isolation")
if calls != [("north", "USER-7"), ("south", "USER-7")]:
    raise SystemExit("oracle_mismatch:loader_call_count")

# Independently run the fixture's exact focused and full test commands.
env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
     "-p", "test_profile_service.py", "-v"], cwd=fixture, env=env)
run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
    cwd=fixture, env=env)

# Candidate copies have no Git history; check whitespace against a temporary
# baseline containing only the editable source.
with tempfile.TemporaryDirectory(prefix="profile-cache-diff-") as temp:
    work = Path(temp)
    candidate = work / "profile_cache.py"
    candidate.write_text(BASELINE_SOURCE, encoding="utf-8")
    run(["git", "init", "-q"], cwd=work)
    run(["git", "config", "user.name", "fixture-oracle"], cwd=work)
    run(["git", "config", "user.email", "fixture-oracle@invalid"], cwd=work)
    run(["git", "add", "profile_cache.py"], cwd=work)
    run(["git", "commit", "-q", "-m", "baseline"], cwd=work)
    candidate.write_bytes((fixture / "profile_cache.py").read_bytes())
    run(["git", "diff", "--check"], cwd=work)

print("oracle_passed:same-tenant reuse, cross-tenant isolation; focused/full tests and diff check passed")
