"""Privacy-safe parsing helpers for pilot CLI receipts.

Only bounded token counters, timing, explicit action kinds, CLI status, and
validated candidate IDs are returned. Raw event content is never retained.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any


MAX_COUNTER = 1_000_000_000
MAX_CANDIDATES = 32
SAFE_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens")
STATUS_VALUES = frozenset({"remote-choice", "no-remote-choice"})
CHOICE_LIST_FIELDS = {
    "strategy": "strategies",
    "test_order": "ordered",
    "triage": "steps",
}
ACTION_TYPES = frozenset({
    "command_execution", "function_call", "tool_call", "mcp_tool_call",
    "collaboration_tool_call", "web_search",
})


def _counter(value: Any) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool)
        and 0 <= value <= MAX_COUNTER
    )


def _timestamp(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def parse_decision_command_output(text: str) -> dict[str, Any] | None:
    """Parse a strict JSON object occupying the whole output or its final suffix.

    Command chatter before the object is tolerated, but trailing non-whitespace,
    duplicate object keys, non-finite numbers, and non-object JSON are rejected.
    The original text is never returned or logged.
    """
    if not isinstance(text, str) or not text:
        return None

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite JSON number")
        return number

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    start = text.find("{")
    if start < 0:
        return None
    decoder = json.JSONDecoder(
        object_pairs_hook=unique_object,
        parse_float=finite_float,
        parse_constant=reject_constant,
    )
    try:
        value, end = decoder.raw_decode(text, start)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return None
    if (not isinstance(value, dict)
            or any(character not in " \t\r\n" for character in text[end:])):
        return None
    return value


def parse_codex_json_events(
    lines: Iterable[str],
    *,
    started_at: float | None = None,
    ended_at: float | None = None,
    event_times: Iterable[float] | None = None,
    validated_useful_action_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Parse Codex CLI --json events into an allowlisted, redacted receipt.

    A usage receipt is scored only when exactly one complete, valid
    turn.completed usage object is present. Duplicate or partial counters are
    unscored, avoiding double counting and ensuring unavailable data stays unscored.
    Event times, when supplied, align by input line and are monotonic seconds.
    A useful action requires a caller-supplied, independently validated item ID
    and a successful completion event; a mere tool start is never sufficient.
    """
    times = list(event_times) if event_times is not None else []
    validated = {value for value in validated_useful_action_ids
                 if isinstance(value, str) and SAFE_ID_RE.fullmatch(value)}
    usage_events: list[dict[str, int] | None] = []
    first_action: dict[str, Any] | None = None
    first_useful: dict[str, Any] | None = None

    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue

        if event.get("type") == "turn.completed":
            raw_usage = event.get("usage")
            if not isinstance(raw_usage, Mapping) or any(
                key not in raw_usage or not _counter(raw_usage[key])
                for key in USAGE_KEYS
            ):
                usage_events.append(None)
            else:
                usage = {key: raw_usage[key] for key in USAGE_KEYS}
                if usage["cached_input_tokens"] > usage["input_tokens"]:
                    usage_events.append(None)
                else:
                    usage_events.append(usage)

        event_type = event.get("type")
        if event_type not in {"item.started", "item.completed"}:
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        action_type = item.get("type")
        if not isinstance(action_type, str) or action_type not in ACTION_TYPES:
            continue
        elapsed = None
        if (started_at is not None and index < len(times)
                and _timestamp(started_at) and _timestamp(times[index])):
            delta = (times[index] - started_at) * 1000
            if math.isfinite(delta) and delta >= 0:
                elapsed = round(delta, 3)
        if event_type == "item.started" and first_action is None:
            first_action = {"type": action_type, "elapsed_ms": elapsed}
        if (event_type == "item.completed" and first_useful is None
                and item.get("id") in validated and item.get("exit_code") == 0):
            first_useful = {"type": action_type, "elapsed_ms": elapsed}

    usage_status = "unscored"
    token_usage = None
    if len(usage_events) == 1 and usage_events[0] is not None:
        usage_status = "available"
        counters = usage_events[0]
        token_usage = {
            **counters,
            "uncached_input_proxy": counters["input_tokens"] - counters["cached_input_tokens"],
        }

    elapsed_ms = None
    if (_timestamp(started_at) and _timestamp(ended_at) and ended_at >= started_at):
        duration = (ended_at - started_at) * 1000
        if math.isfinite(duration):
            elapsed_ms = round(duration, 3)

    return {
        "usage_status": usage_status,
        "token_usage": token_usage,
        "elapsed_ms": elapsed_ms,
        "first_tool_start": first_action,
        "first_useful_action": first_useful,
        "billing_estimate": None,
    }


def build_agent_measurement_receipt(
    lines: Iterable[str],
    timestamps: Iterable[float] | None,
    started_at: float | None,
    ended_at: float | None,
    exit_code: int | None,
    collector_failure: Any = None,
) -> dict[str, Any]:
    """Build one JSON-safe, privacy-reduced CLI measurement receipt.

    The status reports process/collector completion, not task acceptance.
    Any collector failure marker, including an empty value, fails execution
    status even if the subprocess returned zero. Failure details and raw event
    content are never retained. Missing or invalid usage remains unscored.
    """
    parsed = parse_codex_json_events(
        lines,
        started_at=started_at,
        ended_at=ended_at,
        event_times=timestamps,
    )
    valid_exit_code = (
        exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool)
        and -255 <= exit_code <= 255 else None
    )
    collector_failed = collector_failure is not None
    return {
        "status": (
            "completed"
            if valid_exit_code == 0 and not collector_failed
            else "failed"
        ),
        "exit_code": valid_exit_code,
        "collector_failed": collector_failed,
        **parsed,
    }


def parse_choice_receipt(
    payload: str | Mapping[str, Any],
    *,
    choice_type: str,
    candidate_ids: Iterable[str],
) -> dict[str, Any]:
    """Reduce strategy, test-order, or triage JSON to status and validated IDs."""
    allowed_field = CHOICE_LIST_FIELDS.get(choice_type) if isinstance(choice_type, str) else None
    try:
        known_values = list(candidate_ids)
    except TypeError:
        known_values = []
    if (allowed_field is None or not known_values
            or any(not isinstance(item, str) or not SAFE_ID_RE.fullmatch(item) for item in known_values)):
        return {"status": "unscored", "candidate_ids": []}
    known = set(known_values)

    try:
        value = json.loads(payload) if isinstance(payload, str) else payload
    except (TypeError, json.JSONDecodeError):
        return {"status": "unscored", "candidate_ids": []}
    if not isinstance(value, Mapping):
        return {"status": "unscored", "candidate_ids": []}
    status = value.get("status")
    entries = value.get(allowed_field)
    if (not isinstance(status, str) or status not in STATUS_VALUES
            or not isinstance(entries, list) or len(entries) > MAX_CANDIDATES):
        return {"status": "unscored", "candidate_ids": []}

    parsed: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            return {"status": "unscored", "candidate_ids": []}
        identifier = entry.get("id")
        if (not isinstance(identifier, str) or not SAFE_ID_RE.fullmatch(identifier)
                or identifier not in known or identifier in parsed):
            return {"status": "unscored", "candidate_ids": []}
        parsed.append(identifier)

    if status == "remote-choice" and not parsed:
        return {"status": "unscored", "candidate_ids": []}
    result = {"status": status, "candidate_ids": parsed}
    if choice_type == "triage":
        exit_status = value.get("observed_exit_status")
        if (not isinstance(exit_status, int) or isinstance(exit_status, bool)
                or not -255 <= exit_status <= 255
                or value.get("test_failed") is not (exit_status != 0)):
            return {"status": "unscored", "candidate_ids": []}
        result["observed_exit_status"] = exit_status
    return result
