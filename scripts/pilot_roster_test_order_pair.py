#!/usr/bin/env python3
"""Run one bounded, randomized roster-import post-edit test-order pair.

This runner privately reuses the reviewed cross-layer harness with an isolated
module instance and fixture-specific frozen metadata. Treatment advice is
nonbinding, and a local fallback is valid.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "roster_test_order"
CHANGED_FILE = "roster/service.py"
UNIT_COMMAND = "python -m unittest discover -s tests -p 'test_unit*.py' -v"
INTEGRATION_COMMAND = "python -m unittest discover -s tests -p 'test_integration*.py' -v"
REQUIRED_COMMAND = "python -m unittest discover -s tests -v"
RANK_COMMAND = "python -m jevcompass tests rank --input test-options.json --json"
CANDIDATES = (
    {"kind": "unit", "command": UNIT_COMMAND, "relevance": 0.8, "id": "unit"},
    {"kind": "integration", "command": INTEGRATION_COMMAND, "relevance": 0.8, "id": "integration"},
)
REQUIRED = ({"command": REQUIRED_COMMAND, "id": "full"},)
CHOICE_IDS = ("unit", "integration")
BASE_PROMPT = (
    "Implement the roster import contract end-to-end. Normalize trimmed email "
    "addresses to lowercase, retain only the first occurrence of each email, "
    "and preserve first-seen order and the first record's trimmed fields. The "
    "CLI must emit the documented JSON object with count and roster fields and "
    "reject invalid input without JSON output. Read README.md, CONTRACT.md, "
    "TESTING.md, and test-options.json. Change only roster/service.py; do not "
    "edit tests, contract, metadata, or the frozen oracle. You may choose "
    "either focused candidate using the task and metadata, then must run the "
    f"required full suite: {REQUIRED_COMMAND}. Report only command exit status."
)
TREATMENT_RANKING = (
    "\n\nAfter a meaningful edit to roster/service.py and before any focused "
    "test, request one ordering from the identical local test-options.json "
    f"using exactly: {RANK_COMMAND}\n"
    "This command only ranks checks. Asking establishes treatment exposure; "
    "following the returned order is not required. Consider whether its result "
    "is relevant to your change, and reject it or use a local choice if needed. "
    "Advice is nonbinding. Always run the required full suite and preserve failures."
)
SAFE_FAILURES = {
    "unit": "test_import_deduplicates_casefolded_email_preserving_first_seen",
    "integration": "test_cli_emits_roster_json_contract",
}
EXPECTED_CHOICES = {"unit": UNIT_COMMAND, "integration": INTEGRATION_COMMAND}
MANIFEST = FIXTURE / "fixture-manifest.json"
# This digest pins the manifest; the manifest in turn pins all other fixture files.
FIXTURE_MANIFEST_SHA256 = "570e850ad3c9abd1d90d1fa3c8e84fec437d3016dde7929425ed00cea865eba2"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_hashes() -> dict[str, str] | None:
    try:
        if MANIFEST.is_symlink() or not MANIFEST.is_file():
            return None
        if _sha256(MANIFEST) != FIXTURE_MANIFEST_SHA256:
            return None
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or set(manifest) != {"files"}:
            return None
        expected = manifest["files"]
        if not isinstance(expected, dict) or not expected:
            return None
        actual: dict[str, str] = {}
        for path in FIXTURE.rglob("*"):
            if path.is_symlink():
                return None
            if path.is_file() and path != MANIFEST:
                if "__pycache__" in path.parts or path.suffix == ".pyc":
                    continue
                actual[path.relative_to(FIXTURE).as_posix()] = _sha256(path)
        if actual != expected:
            return None
        return actual
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def verify_fixture() -> bool:
    """Return true only for the exact frozen, non-symlink fixture."""
    return _fixture_hashes() is not None


def _load_isolated_cross_layer_runner():
    path = ROOT / "scripts" / "pilot_cross_layer_test_order_pair.py"
    name = "_roster_test_order_cross_layer_harness"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("shared harness unavailable")
    module = importlib.util.module_from_spec(spec)
    # Its harness and imported engine remain private to this module instance.
    spec.loader.exec_module(module)
    return module


runner = _load_isolated_cross_layer_runner()
runner.FIXTURE = FIXTURE
runner.CHANGED_FILE = CHANGED_FILE
runner.UNIT_COMMAND = UNIT_COMMAND
runner.INTEGRATION_COMMAND = INTEGRATION_COMMAND
runner.REQUIRED_COMMAND = REQUIRED_COMMAND
runner.RANK_COMMAND = RANK_COMMAND
runner.CANDIDATES = CANDIDATES
runner.REQUIRED = REQUIRED
runner.CHOICE_IDS = CHOICE_IDS
runner.BASE_PROMPT = BASE_PROMPT
runner.TREATMENT_RANKING = TREATMENT_RANKING
runner.SAFE_FAILURES = SAFE_FAILURES
runner.SOURCE_PATH_MARKERS = (
    CHANGED_FILE,
    CHANGED_FILE.replace("/", "\\"),
)
runner.EXPECTED_CHOICES = EXPECTED_CHOICES

# Synchronize only the private engine instance loaded by this wrapper.
for key, value in {
    "FIXTURE": FIXTURE,
    "CHANGED_FILE": CHANGED_FILE,
    "UNIT_COMMAND": UNIT_COMMAND,
    "CONTRACT_COMMAND": INTEGRATION_COMMAND,
    "REQUIRED_COMMAND": REQUIRED_COMMAND,
    "CANDIDATES": CANDIDATES,
    "REQUIRED": REQUIRED,
    "CHOICE_IDS": CHOICE_IDS,
    "BASE_PROMPT": BASE_PROMPT,
    "TREATMENT_RANKING": TREATMENT_RANKING,
}.items():
    setattr(runner.engine, key, value)


_original_arm = runner.engine._run_arm


def _run_arm_with_frozen_oracle(**kwargs: Any) -> dict[str, Any]:
    result = _original_arm(**kwargs)
    fixture = kwargs["fixture"]
    env = os.environ.copy()
    for key in ("OPENROUTER_API_KEY", "JEV_API_KEY", "JEVCOMPASS_API_KEY"):
        env.pop(key, None)
    for key in tuple(env):
        if key.startswith("JEV_"):
            env.pop(key, None)
    start = time.monotonic()
    try:
        completed = subprocess.run(
            [sys.executable, "tests/frozen_oracle.py"],
            cwd=fixture,
            capture_output=True,
            timeout=runner.engine.MAX_TIMEOUT,
            check=False,
            env=env,
        )
        oracle = {
            "exit_code": completed.returncode,
            "wall_ms": round((time.monotonic() - start) * 1000, 2),
        }
    except subprocess.TimeoutExpired:
        oracle = {"exit_code": None, "wall_ms": None, "status": "timeout"}
    except OSError:
        oracle = {"exit_code": None, "wall_ms": None, "status": "unavailable"}

    validation = result.get("independent_validation")
    if not isinstance(validation, dict):
        validation = {}
    validation["frozen_oracle"] = oracle
    result["independent_validation"] = validation
    if oracle.get("exit_code") != 0:
        result["independent_final_validation_status"] = "failed"
    return result


runner.engine._run_arm = _run_arm_with_frozen_oracle


def run_pair(**kwargs: Any) -> dict[str, Any]:
    if not verify_fixture():
        raise ValueError("frozen roster fixture verification failed")
    kwargs.setdefault("fixture_source", FIXTURE)
    kwargs.setdefault("baseline_first_candidate_policy", "either")
    kwargs.setdefault("advice_policy", "nonbinding")
    return runner.run_pair(**kwargs)


# Route the common CLI entrypoint through the fixture-checked nonbinding profile.
runner.engine.run_pair = run_pair


def main(argv: list[str] | None = None) -> int:
    if not verify_fixture():
        print(json.dumps({"status": "failed", "failure": "fixture_integrity_failed"}))
        return 2
    return runner.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
