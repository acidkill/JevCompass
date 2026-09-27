#!/usr/bin/env python3
"""Matched coding-strategy experiment; all advice remains nonbinding."""
import hashlib
import importlib.util
import subprocess
import sys
import json
from pathlib import Path

from pilot_receipts import parse_choice_receipt

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "_strategy_pair_engine", ROOT / "scripts" / "pilot_test_order_pair.py")
engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engine)
STRATEGY_COMMAND = (
    "python -m jevcompass strategy choose --kind coding "
    "--signal existing_symbol --signal behavior_change --json"
)
STRATEGY_IDS = ["inspect_dependency_or_symbol_use", "define_contract_then_implement"]
# Both approaches apply to the same behavior change and existing callable.
# This is an ordering experiment, not hidden knowledge about the repair.
engine.TREATMENT_RANKING = (
    "\nBefore editing, optionally consult the pretask strategy selector using exactly "
    + STRATEGY_COMMAND +
    ". Use only returned known strategy identifiers. If unavailable, uncertain or "
    "not useful, continue locally. Do not omit focused or full required validation."
)
_original_events = engine._event_receipts


def _event_receipts(lines, times, started):
    lines = list(lines)
    result = _original_events(lines, times, started)
    pending = {}
    seen = set()
    payloads = []
    choice = {"status": "unscored", "candidate_ids": []}
    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        item = engine._item(event)
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not 0 < len(identifier) <= 128:
            continue
        exact = engine._command_argv(item) == engine.shlex.split(STRATEGY_COMMAND)
        if event.get("type") == "item.started" and exact and identifier not in seen:
            seen.add(identifier)
            pending[identifier] = times[index] if index < len(times) else None
        elif event.get("type") == "item.completed" and identifier in pending:
            call_start = pending.pop(identifier)
            output = item.get("aggregated_output", item.get("output"))
            if not isinstance(output, str):
                continue
            elapsed = ((times[index] - call_start) * 1000
                       if call_start is not None and index < len(times) else None)
            payloads.append((elapsed, output))
            exit_code = item.get("exit_code")
            if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code == 0:
                choice = parse_choice_receipt(
                    output, choice_type="strategy", candidate_ids=STRATEGY_IDS)
    result["strategy"] = choice
    result["strategy_usage"] = engine._rank_usage(payloads, len(seen))
    result["strategy_latency_ms"] = payloads[-1][0] if payloads else None
    result["pretask_order_status"] = "unscored_edit_order"
    return result


engine._event_receipts = _event_receipts


def frozen_files(root):
    """Include immutable files and symlinks without following link targets."""
    result = {}
    for path in root.rglob("*"):
        relative = str(path.relative_to(root))
        if "__pycache__" in path.parts or relative == engine.CHANGED_FILE:
            continue
        if path.is_symlink():
            result[relative] = "symlink:" + str(path.readlink())
        elif path.is_file():
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


_original_arm = engine._run_arm


def _run_arm(**kwargs):
    fixture = kwargs["fixture"]
    before = frozen_files(fixture)
    result = _original_arm(**kwargs)
    changed = fixture / engine.CHANGED_FILE
    intact = before == frozen_files(fixture) and changed.is_file() and not changed.is_symlink()
    result["immutable_files_preserved"] = intact
    independent = {}
    for kind, command in [("unit", engine.UNIT_COMMAND),
                          ("contract", engine.CONTRACT_COMMAND),
                          ("required", engine.REQUIRED_COMMAND)]:
        code = None
        if intact:
            argv = engine.shlex.split(command)
            argv[0] = sys.executable
            try:
                completed = subprocess.run(argv, cwd=fixture, capture_output=True, timeout=10)
                code = completed.returncode
            except (OSError, subprocess.TimeoutExpired):
                pass
        independent[kind] = code
    result["independent_validation"] = independent
    result["independent_quality_status"] = (
        "frozen_checks_pass" if intact and all(code == 0 for code in independent.values())
        else "failed_or_unavailable"
    )
    if result["independent_quality_status"] != "frozen_checks_pass":
        result["cli_status"] = "failed"
    return result


engine._run_arm = _run_arm


def main(argv=None):
    return engine.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
