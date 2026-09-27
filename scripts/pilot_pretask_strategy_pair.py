#!/usr/bin/env python3
"""Compare supervisor-prepared advice with baseline; not native hook evidence."""
import importlib.util
import json
import math
import re
from pathlib import Path
import subprocess
import sys
import time

from pilot_receipts import parse_choice_receipt

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "_pretask_strategy_engine", ROOT / "scripts" / "pilot_strategy_pair.py")
strategy_runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(strategy_runner)
engine = strategy_runner.engine
engine.__doc__ = __doc__
engine.OPENROUTER_KEY_HELP = "opt in to supervisor-only API access; neither agent receives the key"
MARKER = "\n[SUPERVISOR_PRETASK_STRATEGY]"
engine.TREATMENT_RANKING = MARKER
engine.BASE_PROMPT += (
    "\nDeclared focused checks: " + engine.UNIT_COMMAND + " OR "
    + engine.CONTRACT_COMMAND + ". Choose one and run it as a separate command, "
    "then run the required full suite as a separate command. "
    "Read test-options.json for the same candidate definitions."
)
COMMAND = [
    sys.executable, "-m", "jevcompass", "strategy", "choose",
    "--kind", "coding", "--signal", "existing_symbol",
    "--signal", "behavior_change", "--json",
]
ACTIONS = {
    "inspect_dependency_or_symbol_use":
        "Inspect existing symbol use before editing.",
    "define_contract_then_implement":
        "Define expected behavior, then implement against it.",
}
_expected_ids = ()
_original_events = engine._event_receipts


def _event_receipts(lines, times, started):
    lines = list(lines)
    result = _original_events(lines, times, started)
    acknowledgment = "not_observed"
    unmatched = {"unittest": 0, "pytest": 0}
    completed_ids = set()
    first_tool_seen = False
    expected = "JevCompass strategy receipt: " + ",".join(_expected_ids)
    for line in lines:
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        item = engine._item(event)
        if (event.get("type") == "item.completed" and isinstance(item, dict)
                and item.get("type") == "command_execution"):
            identifier = item.get("id")
            if (isinstance(identifier, str) and 0 < len(identifier) <= 128
                    and identifier not in completed_ids):
                completed_ids.add(identifier)
                argv = engine._command_argv(item) or []
                if engine._test_kind(item) is None:
                    for family in unmatched:
                        if family in argv:
                            unmatched[family] += 1
        kind, message, _tool = engine.core.extract_event(event)
        if kind == "tool":
            first_tool_seen = True
        elif (kind == "assistant" and _expected_ids and isinstance(message, str)
              and expected in [part.strip() for part in message.splitlines()]
              and acknowledgment == "not_observed"):
            acknowledgment = ("after_first_tool" if first_tool_seen
                              else "before_first_tool")
    result["unmatched_test_command_counts"] = unmatched
    result["pretask_acknowledgment"] = acknowledgment
    counters = engine.parse_codex_json_events(
        lines, started_at=started, event_times=times)
    first_tool = counters["first_tool_start"]
    result["first_tool_start_ms"] = (
        first_tool["elapsed_ms"] if first_tool else None)
    result["first_successful_relevant_check_ms"] = None
    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = engine._item(event)
        if not isinstance(item, dict) or engine._test_kind(item) not in {"unit", "contract"}:
            continue
        output = item.get("aggregated_output", item.get("output"))
        code = item.get("exit_code")
        if (type(code) is int and code == 0 and isinstance(output, str)
                and re.search(r"^Ran [1-9][0-9]* tests? in ", output, re.MULTILINE)
                and re.search(r"^OK\s*$", output, re.MULTILINE)
                and index < len(times) and type(times[index]) in {int, float}
                and math.isfinite(times[index]) and times[index] >= started):
            result["first_successful_relevant_check_ms"] = round(
                (times[index] - started) * 1000, 2)
            break
    return result


engine._event_receipts = _event_receipts
_original_arm = engine._run_arm


def _prepare(home, allow_key):
    env = engine.core._isolated_environment(
        home=home, isolated_python=home / "python", allow_openrouter_key=allow_key)
    try:
        completed = subprocess.run(
            COMMAND, env=env, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=2, cwd=ROOT)
        if completed.returncode != 0:
            return {"status": "unscored", "candidate_ids": []}, None
        output = completed.stdout.decode("utf-8")
        if len(output) > engine.MAX_CHOICE_OUTPUT_BYTES:
            return {"status": "unscored", "candidate_ids": []}, None
        def unique_pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate-key")
                result[key] = value
            return result
        payload = json.loads(output, object_pairs_hook=unique_pairs)
        choice = parse_choice_receipt(
            payload, choice_type="strategy", candidate_ids=ACTIONS)
        usage = engine._rank_usage([(0, output)], 1)
        return choice, usage
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError, TypeError):
        return {"status": "unscored", "candidate_ids": []}, None


def _run_arm(**kwargs):
    global _expected_ids
    treatment = kwargs["prompt"].endswith(MARKER)
    started = time.monotonic()
    choice = {"status": "not_invoked", "candidate_ids": []}
    usage = None
    if treatment:
        choice, usage = _prepare(kwargs["home"], kwargs["allow_openrouter_key"])
    ids = tuple(choice["candidate_ids"])
    prompt = kwargs["prompt"].removesuffix(MARKER)
    if ids:
        prompt += (
            "\nOptional JevCompass pretask advice (already prepared): "
            + " ".join(ACTIONS[identifier] for identifier in ids)
            + " Continue locally if not useful; preserve all required validation."
        )
    prompt += (
        "\nBefore your first tool call, report exactly: JevCompass strategy receipt: "
        + (",".join(ids) if ids else "none")
    )
    prepared_ms = (time.monotonic() - started) * 1000
    _expected_ids = ids or ("none",)
    try:
        # Neither agent receives the provider key or network opt-in.
        result = _original_arm(**{
            **kwargs, "prompt": prompt, "allow_openrouter_key": False})
    finally:
        _expected_ids = ()
    agent_ms = result.get("completion_ms")
    result["agent_completion_ms"] = agent_ms
    result["completion_ms"] = (round(agent_ms + prepared_ms, 2)
                               if isinstance(agent_ms, (int, float)) else None)
    for field in ("first_useful_error_ms", "first_observed_focused_failure_ms",
                  "first_tool_start_ms", "first_successful_relevant_check_ms"):
        if isinstance(result.get(field), (int, float)):
            result[field] = round(result[field] + prepared_ms, 2)
    if result.get("independent_quality_status") != "frozen_checks_pass":
        result["first_successful_relevant_check_ms"] = None
    result["strategy"] = choice
    result["strategy_usage"] = usage
    result["strategy_preparation_ms"] = round(prepared_ms, 2)
    result["pretask_order_status"] = (
        "initial_prompt_before_agent_launch" if ids else "no_advice")
    result.setdefault("pretask_acknowledgment", "not_observed")
    return result


engine._run_arm = _run_arm


def main(argv=None):
    return engine.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
