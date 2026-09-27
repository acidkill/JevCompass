#!/usr/bin/env python3
"""Run one bounded matched CLI pair for post-change cross-layer test ordering.

Both arms receive the same synthetic checkout task and candidate metadata. The
treatment additionally requests a test rank after changing checkout/service.py.
Prompts, raw events, test output, and source text are never written to receipts.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import selectors
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "_cross_layer_test_order_engine", ROOT / "scripts" / "pilot_test_order_pair.py"
)
engine = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(engine)

FIXTURE = ROOT / "tests" / "fixtures" / "cross_layer_test_order"
CHANGED_FILE = "checkout/service.py"
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
    "Implement the documented two-percent half-up checkout service fee. Read "
    "README.md, CONTRACT.md, TESTING.md, and test-options.json. Change only "
    "checkout/service.py; do not edit contracts or tests. The ordinary local "
    "policy is to run the faster unit candidate first, while recognizing that "
    "integration covers the public JSON mapping and remains mandatory in the "
    "full suite. After the source change, run one focused candidate and always "
    f"run the required full suite: {REQUIRED_COMMAND}. Report command exit "
    "statuses without including source text."
)
TREATMENT_RANKING = (
    "\n\nAfter a meaningful edit to checkout/service.py has completed, and "
    "before starting any focused candidate, request the order from the same "
    "local test-options.json by running exactly:\n"
    f"{RANK_COMMAND}\n"
    "The rank command does not execute tests. Follow its first candidate when "
    "the returned order is valid. If it abstains, fails, or returns invalid "
    "data, use the shared faster-unit-first local policy. Then run the required "
    "full suite. Do not call Jev before changing checkout/service.py."
)
engine.FIXTURE = FIXTURE
engine.CHANGED_FILE = CHANGED_FILE
engine.MAX_ARTIFACT_BYTES = min(engine.MAX_ARTIFACT_BYTES, 64 * 1024)
engine.UNIT_COMMAND = UNIT_COMMAND
engine.CONTRACT_COMMAND = INTEGRATION_COMMAND
engine.REQUIRED_COMMAND = REQUIRED_COMMAND
engine.CANDIDATES = CANDIDATES
engine.REQUIRED = REQUIRED
engine.CHOICE_IDS = CHOICE_IDS
engine.BASE_PROMPT = BASE_PROMPT
engine.TREATMENT_RANKING = TREATMENT_RANKING

SAFE_FAILURES = {
    "unit": "test_half_cent_rounds_half_up",
    "integration": "test_success_json_contract",
}
SOURCE_PATH_MARKERS = (CHANGED_FILE, CHANGED_FILE.replace("/", "\\"))
EDIT_WORDS = re.compile(
    r"\b(?:apply_patch|write_file|edit_file|replace_file|file_change|touch|tee)\b"
    r"|(?:^|\s)(?:>>?|--write)\s*\S",
    re.I,
)
EXPECTED_CHOICES = {
    "unit": UNIT_COMMAND,
    "integration": INTEGRATION_COMMAND,
}


def _item(event: dict[str, Any]) -> dict[str, Any] | None:
    return engine._item(event)


def _event_id(item: dict[str, Any]) -> str | None:
    value = item.get("id")
    return value if isinstance(value, str) and 0 < len(value) <= 128 else None


def _event_command(item: dict[str, Any]) -> str:
    parts: list[str] = []
    raw = item.get("command")
    if isinstance(raw, str):
        parts.append(raw)
    elif isinstance(raw, list) and all(isinstance(part, str) for part in raw):
        parts.append(" ".join(raw))
    for key in ("name", "tool_name", "tool"):
        value = item.get(key)
        if isinstance(value, str):
            parts.append(value)
    for key in ("arguments", "input", "tool_input", "file_path", "path"):
        value = item.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (dict, list)):
            try:
                parts.append(json.dumps(value, sort_keys=True, separators=(",", ":")))
            except (TypeError, ValueError):
                pass
    return "\n".join(parts)


def _targets_changed_file(item: dict[str, Any]) -> bool:
    text = _event_command(item).replace("\\", "/")
    return any(marker.replace("\\", "/") in text for marker in SOURCE_PATH_MARKERS)


def _source_edit_event(item: dict[str, Any]) -> bool:
    text = _event_command(item)
    return _targets_changed_file(item) and bool(EDIT_WORDS.search(text))


def _test_kind(item: dict[str, Any]) -> str | None:
    argv = engine._command_argv(item)
    if argv == shlex.split(UNIT_COMMAND):
        return "unit"
    if argv == shlex.split(INTEGRATION_COMMAND):
        return "integration"
    if argv == shlex.split(REQUIRED_COMMAND):
        return "required"
    return None


def _strict_object(text: str) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate-json-key")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=pairs)


def _validated_choice(payload: str | None) -> dict[str, Any]:
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > engine.MAX_CHOICE_OUTPUT_BYTES:
        return {"status": "unscored", "candidate_ids": []}
    try:
        value = _strict_object(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"status": "unscored", "candidate_ids": []}
    expected_required = [{"id": "full", "command": REQUIRED_COMMAND}]
    ordered = value.get("ordered") if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get("executed") is not False
            or not isinstance(ordered, list) or len(ordered) != len(EXPECTED_CHOICES)
            or value.get("required") != expected_required):
        return {"status": "unscored", "candidate_ids": []}
    seen: set[str] = set()
    for candidate in ordered:
        if not isinstance(candidate, dict):
            return {"status": "unscored", "candidate_ids": []}
        identifier = candidate.get("id")
        if (identifier not in EXPECTED_CHOICES
                or candidate.get("command") != EXPECTED_CHOICES[identifier]
                or candidate.get("kind") != identifier or identifier in seen):
            return {"status": "unscored", "candidate_ids": []}
        seen.add(identifier)
    return engine.parse_choice_receipt(
        value, choice_type="test_order", candidate_ids=CHOICE_IDS
    )


engine._test_kind = _test_kind
engine._validated_choice = _validated_choice
_original_event_receipts = engine._event_receipts


def _event_receipts(
    lines: Iterable[str], event_times: list[float], started: float,
) -> dict[str, Any]:
    raw_lines = list(lines)
    # The shared legacy accumulator names the third candidate kind "contract".
    # Map our integration command to that internal slot only during this call.
    engine._test_kind = lambda item: (
        "contract" if _test_kind(item) == "integration" else _test_kind(item)
    )
    try:
        base = _original_event_receipts(raw_lines, event_times, started)
    finally:
        engine._test_kind = _test_kind
    seen_starts: set[str] = set()
    seen_completions: set[str] = set()
    pending: dict[str, tuple[str, int, str]] = {}
    focused: dict[str, int] = {}
    focused_order: list[str] = []
    required_exits: list[int] = []
    rank_starts: list[tuple[str, int, float]] = []
    rank_completions: list[tuple[str, int, float, int | None]] = []
    source_edit_starts: dict[str, tuple[int, float]] = {}
    source_edit_completions: list[tuple[int, float]] = []
    first_observed_failure_ms: float | None = None
    first_useful_error_ms: float | None = None
    first_useful_candidate: str | None = None
    focused_start_orders: list[int] = []
    completed_focused_ids: set[str] = set()
    required_invocations: set[str] = set()

    def elapsed(index: int) -> float | None:
        if index >= len(event_times):
            return None
        value = event_times[index]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < started):
            return None
        return round((value - started) * 1000, 2)

    for index, line in enumerate(raw_lines):
        try:
            event = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        item = _item(event)
        if not isinstance(item, dict):
            continue
        identifier = _event_id(item)
        if identifier is None:
            continue
        command_kind = _test_kind(item)
        is_rank = engine._rank_invocation(item)
        is_edit = _source_edit_event(item)
        if event_type == "item.started":
            if identifier in seen_starts:
                continue
            seen_starts.add(identifier)
            if command_kind in {"unit", "integration"}:
                pending[identifier] = (command_kind, index, "focused")
                focused_start_orders.append(index)
                if command_kind not in focused_order:
                    focused_order.append(command_kind)
            elif command_kind == "required":
                pending[identifier] = (command_kind, index, "required")
                required_invocations.add(identifier)
            if is_rank:
                rank_starts.append((identifier, index, event_times[index] if index < len(event_times) else float("nan")))
                pending[identifier] = ("rank", index, "rank")
            if is_edit:
                source_edit_starts[identifier] = (
                    index, event_times[index] if index < len(event_times) else float("nan")
                )
                pending[identifier] = ("source_edit", index, "source_edit")
        elif event_type == "item.completed":
            if identifier in seen_completions:
                continue
            pending_entry = pending.pop(identifier, None)
            if pending_entry is None:
                continue
            kind, _start_index, role = pending_entry
            if command_kind is not None and command_kind != kind:
                continue
            code = item.get("exit_code")
            if isinstance(code, bool) or not isinstance(code, int):
                continue
            seen_completions.add(identifier)
            if role == "focused":
                focused[kind] = code
                completed_focused_ids.add(identifier)
                if code != 0 and first_observed_failure_ms is None:
                    first_observed_failure_ms = elapsed(index)
                output = item.get("aggregated_output")
                if not isinstance(output, str):
                    output = item.get("output")
                marker = SAFE_FAILURES[kind]
                if (code != 0 and first_useful_error_ms is None
                        and isinstance(output, str)
                        and re.search(r"(?:^|\n)(?:FAIL|ERROR):\s*" + re.escape(marker) + r"\b", output)):
                    first_useful_error_ms = elapsed(index)
                    first_useful_candidate = kind
            elif role == "required":
                required_exits.append(code)
            if identifier in source_edit_starts and code == 0:
                source_edit_completions.append(
                    (index, event_times[index] if index < len(event_times) else float("nan"))
                )
            if any(rank[0] == identifier for rank in rank_starts):
                rank_completions.append(
                    (identifier, index,
                     event_times[index] if index < len(event_times) else float("nan"), code)
                )

    first_rank = rank_starts[0] if rank_starts else None
    first_rank_completion = (
        next((event for event in rank_completions if first_rank and event[0] == first_rank[0]), None)
    )
    first_focus_order = min(focused_start_orders) if focused_start_orders else None
    qualifying_edits = [
        (order, event_time) for order, event_time in source_edit_completions
        if first_rank and order < first_rank[1]
    ]
    source_edit_order = max((order for order, _ in qualifying_edits), default=None)
    source_edit_time = next(
        (event_time for order, event_time in reversed(qualifying_edits)
         if order == source_edit_order),
        float("nan"),
    )
    later_edits_before_focus = bool(
        first_rank and first_focus_order is not None
        and any(first_rank[1] < order < first_focus_order
                for order, _ in source_edit_completions)
    )
    rank_before_focus = bool(
        first_rank and first_rank_completion and first_focus_order is not None
        and first_rank_completion[1] < first_focus_order
    )
    edit_before_rank = source_edit_order is not None
    rank_phase_order = (
        "verified_order"
        if len(rank_starts) == 1 and len(rank_completions) == 1
        and edit_before_rank and rank_before_focus and not later_edits_before_focus
        else "unscored"
    )
    rank_start_time = first_rank[2] if first_rank else float("nan")
    edit_to_rank_ms = None
    if (edit_before_rank and math.isfinite(source_edit_time)
            and math.isfinite(rank_start_time) and rank_start_time >= source_edit_time):
        edit_to_rank_ms = round((rank_start_time - source_edit_time) * 1000, 2)

    focus_results = [
        {"candidate_id": candidate, "exit_code": focused[candidate]}
        for candidate in CHOICE_IDS if candidate in focused
    ]
    selected = focused_order[0] if focused_order else None
    choice = base.get("choice", {"status": "unscored", "candidate_ids": []})
    rank_valid = choice.get("status") in {"remote-choice", "no-remote-choice"}
    expected_first = (
        choice.get("candidate_ids", [None])[0]
        if rank_valid and choice.get("candidate_ids")
        else "unit"
    )
    follow_status = "unscored" if selected is None else (
        "followed" if selected == expected_first else "mismatch"
    )

    base.update({
        "choice": choice,
        "focused_test_exits": focus_results,
        "focused_invocation_count": len(completed_focused_ids),
        "focused_candidate_order": focused_order,
        "first_focused_candidate": selected,
        "first_observed_focused_failure_ms": first_observed_failure_ms,
        "first_useful_error_ms": first_useful_error_ms,
        "first_useful_error_status": "observed" if first_useful_error_ms is not None else "not_observed",
        "first_useful_error_candidate": first_useful_candidate,
        "first_useful_error_event_count": 1 if first_useful_error_ms is not None else 0,
        "required_suite_exit": required_exits[-1] if required_exits else None,
        "required_suite_invocation_observed": bool(required_invocations),
        "required_suite_invocation_count": len(required_invocations),
        "rank_invocation_count": len(rank_starts),
        "rank_phase_order_status": rank_phase_order,
        "source_edit_completed_before_rank": edit_before_rank,
        "rank_completed_before_first_focused": rank_before_focus,
        "source_edit_between_rank_and_first_focused": later_edits_before_focus,
        "source_change_to_rank_start_ms": edit_to_rank_ms,
        "first_rank_start_order": first_rank[1] if first_rank else None,
        "first_rank_completion_order": first_rank_completion[1] if first_rank_completion else None,
        "first_focused_start_order": first_focus_order,
        "choice_follow_status": follow_status,
    })
    return base


engine._event_receipts = _event_receipts
_original_run_arm = engine._run_arm


def _digest_source(fixture: Path) -> bytes | None:
    path = fixture / CHANGED_FILE
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > engine.MAX_ARTIFACT_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _frozen_files(fixture: Path) -> dict[str, str]:
    return {
        str(path.relative_to(fixture)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in fixture.rglob("*")
        if path.is_file() and not path.is_symlink()
        and "__pycache__" not in path.parts and path.suffix != ".pyc"
        and str(path.relative_to(fixture)) != CHANGED_FILE
    }


def _independent_final_validation(fixture: Path) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for identifier, command in (
        ("unit", UNIT_COMMAND),
        ("integration", INTEGRATION_COMMAND),
        ("required", REQUIRED_COMMAND),
    ):
        started = time.monotonic()
        try:
            completed = subprocess.run(
                shlex.split(command), cwd=fixture, capture_output=True,
                timeout=engine.MAX_TIMEOUT, check=False,
            )
            elapsed = round((time.monotonic() - started) * 1000, 2)
            results[identifier] = {
                "exit_code": completed.returncode,
                "wall_ms": elapsed,
            }
        except subprocess.TimeoutExpired:
            results[identifier] = {"exit_code": None, "wall_ms": None, "status": "timeout"}
        except OSError:
            results[identifier] = {"exit_code": None, "wall_ms": None, "status": "unavailable"}
    return results


def _collect_events_live(
    process: subprocess.Popen[bytes], *, started: float, timeout: int,
    preserve_on_failure: bool = False, fixture: Path, before_source: bytes | None,
    observation: dict[str, Any],
) -> tuple[list[str], list[float], str | None]:
    """Collect bounded events and snapshot changed source at rank-start receipt."""
    if process.stdout is None:
        return [], [], "codex_unavailable"
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = bytearray()
    lines: list[str] = []
    times: list[float] = []
    total = 0
    failure = None
    deadline = started + timeout

    def append_line(raw: bytes) -> None:
        line = raw.decode("utf-8", errors="replace")
        lines.append(line)
        observed_at = time.monotonic()
        times.append(observed_at)
        if observation.get("rank_source_snapshot_observed"):
            return
        try:
            event = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if not isinstance(event, dict) or event.get("type") != "item.started":
            return
        item = _item(event)
        if not isinstance(item, dict) or not engine._rank_invocation(item):
            return
        current = _digest_source(fixture)
        observation["rank_source_snapshot_observed"] = current is not None
        observation["rank_source_snapshot_changed"] = (
            current is not None and before_source is not None and current != before_source
        )
        observation["rank_source_snapshot_ms"] = round((observed_at - started) * 1000, 2)

    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "timeout"
                break
            for key, _ in selector.select(min(remaining, 0.25)):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > engine.core.MAX_EVENT_BYTES:
                    failure = "event_output_limit"
                    break
                pending.extend(chunk)
                while True:
                    newline = pending.find(b"\n")
                    if newline < 0:
                        break
                    append_line(bytes(pending[:newline]))
                    del pending[:newline + 1]
            if failure:
                break
        if pending and failure is None:
            append_line(bytes(pending))
    finally:
        selector.close()
        process.stdout.close()
    if failure:
        process.kill()
        process.wait()
        return (lines, times, failure) if preserve_on_failure else ([], [], failure)
    try:
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return (lines, times, "timeout") if preserve_on_failure else ([], [], "timeout")
    return lines, times, None


def _run_arm(**kwargs: Any) -> dict[str, Any]:
    fixture = kwargs["fixture"]
    before_source = _digest_source(fixture)
    immutable_before = _frozen_files(fixture)
    observation: dict[str, Any] = {}
    original_collector = engine.core._collect_events

    def collect(process: subprocess.Popen[bytes], *, started: float, timeout: int,
                preserve_on_failure: bool = False):
        return _collect_events_live(
            process, started=started, timeout=timeout,
            preserve_on_failure=preserve_on_failure, fixture=fixture,
            before_source=before_source, observation=observation,
        )

    engine.core._collect_events = collect
    try:
        result = _original_run_arm(**kwargs)
    finally:
        engine.core._collect_events = original_collector
    after_source = _digest_source(fixture)
    changed = before_source is not None and after_source is not None and before_source != after_source
    preserved = immutable_before == _frozen_files(fixture)
    result["changed_source_observed"] = changed
    result["immutable_files_preserved"] = preserved
    result["independent_validation"] = _independent_final_validation(fixture)
    prompt = kwargs.get("prompt", "")
    treatment = TREATMENT_RANKING in prompt
    if treatment:
        phase_order = result.get("rank_phase_order_status") == "verified_order"
        snapshot_observed = observation.get("rank_source_snapshot_observed") is True
        snapshot_changed = observation.get("rank_source_snapshot_changed") is True
        result["rank_source_snapshot_observed"] = snapshot_observed
        result["rank_source_snapshot_changed"] = snapshot_changed
        result["rank_source_snapshot_ms"] = observation.get("rank_source_snapshot_ms")
        verified = changed and phase_order and snapshot_observed and snapshot_changed
        result["post_change_rank_phase_status"] = "verified" if verified else "unscored"
        if not snapshot_observed:
            result["post_change_rank_phase_reason"] = "rank_source_snapshot_unavailable"
        elif not snapshot_changed:
            result["post_change_rank_phase_reason"] = "source_not_changed_at_rank"
        elif not changed:
            result["post_change_rank_phase_reason"] = "changed_source_not_observed"
        elif not phase_order:
            result["post_change_rank_phase_reason"] = "event_order_not_verified"
    else:
        result["post_change_rank_phase_status"] = "not_applicable"
    result["billing_status"] = "unknown"
    final_all_pass = all(
        result["independent_validation"].get(key, {}).get("exit_code") == 0
        for key in ("unit", "integration", "required")
    )
    result["independent_final_validation_status"] = (
        "passed" if final_all_pass and preserved else "failed"
    )
    if not preserved:
        result["independent_final_validation_status"] = "failed"
    return result


engine._run_arm = _run_arm
_original_pair = engine.run_pair


def _private_receipt_update(output_dir: Path, receipt: dict[str, Any]) -> None:
    target = output_dir / "receipt.json"
    if target.is_symlink() or not target.is_file():
        raise ValueError("private receipt is unavailable")
    data = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".receipt-", suffix=".tmp", dir=output_dir)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def run_pair(**kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("fixture_source", FIXTURE)
    receipt = _original_pair(**kwargs)
    arms = receipt.get("arms")
    if not isinstance(arms, dict) or len(arms) != 2:
        return receipt

    mapping_path = kwargs["output_dir"] / "arm-map.json"
    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        mapping = {}
    treatment_label = next(
        (label for label in ("arm-a", "arm-b") if mapping.get(label) == "treatment"),
        None,
    )
    baseline_label = next(
        (label for label in ("arm-a", "arm-b") if mapping.get(label) == "baseline"),
        None,
    )
    treatment = arms.get(treatment_label, {}) if treatment_label else {}
    baseline = arms.get(baseline_label, {}) if baseline_label else {}
    gates: list[str] = []
    for label, result in arms.items():
        if result.get("cli_status") != "completed":
            gates.append(f"{label}_cli_incomplete")
        if result.get("required_suite_exit") != 0 or not result.get("required_suite_invocation_observed"):
            gates.append(f"{label}_required_suite_unverified")
        if not result.get("focused_test_exits") or not any(
            item.get("exit_code") == 0 for item in result.get("focused_test_exits", [])
        ):
            gates.append(f"{label}_focused_suite_unverified")
        if result.get("independent_final_validation_status") != "passed":
            gates.append(f"{label}_independent_final_validation_failed")
        if not result.get("changed_source_observed"):
            gates.append(f"{label}_source_change_unverified")
        if not result.get("immutable_files_preserved"):
            gates.append(f"{label}_immutable_files_changed")
    if treatment.get("post_change_rank_phase_status") != "verified":
        gates.append("treatment_post_change_rank_phase_unverified")
    if treatment.get("choice_follow_status") != "followed":
        gates.append("treatment_choice_not_followed")
    if baseline.get("first_focused_candidate") != "unit":
        gates.append("baseline_unit_first_policy_not_observed")

    receipt["quality_gate_status"] = "passed" if not gates else "failed"
    receipt["quality_gate_failures"] = sorted(set(gates))
    receipt["status"] = "completed" if not gates else "failed"
    receipt["first_useful_error_semantics"] = (
        "first completed focused invocation whose matched output contains its frozen failure identifier"
    )
    receipt["first_useful_error_counts_are_deduplicated"] = True
    _private_receipt_update(kwargs["output_dir"], receipt)
    return receipt


engine.run_pair = run_pair


def main(argv: list[str] | None = None) -> int:
    return engine.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
