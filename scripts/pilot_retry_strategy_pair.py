#!/usr/bin/env python3
"""Prepare a bounded local contract-route pair; this is not remote efficacy."""
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

from pilot_receipts import parse_choice_receipt

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "retry_review"
VERIFY_CONTRACT = ROOT / "scripts" / "verify_retry_contract.py"
VERIFIER_SHA256 = hashlib.sha256(VERIFY_CONTRACT.read_bytes()).hexdigest()
SPEC = importlib.util.spec_from_file_location(
    "_retry_strategy_pair_engine", ROOT / "scripts" / "pilot_test_order_pair.py"
)
engine = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(engine)

# This rubric is frozen before any future live pair. It classifies evidence in
# the supplied synthetic fixture only; it does not evaluate advice quality.
CONTRACT_EVIDENCE = "consistent"
FROZEN_RUBRIC = (
    "The fixture's RETRY_CONTRACT.md clearly defines the existing public API, "
    "attempt bound, retryable exception/status classes, delay precedence, and "
    "exhaustion behavior. Existing tests exercise only a subset, so the written "
    "contract is the authority and the independent 10-check verifier is the "
    "acceptance gate. Contract-evidence=consistent is used only to select a "
    "local strategy route; it makes no claim about remote-model efficacy."
)
FROZEN_RUBRIC_SHA256 = hashlib.sha256(FROZEN_RUBRIC.encode("utf-8")).hexdigest()

STRATEGY_COMMAND = [
    sys.executable, "-m", "jevcompass", "strategy", "choose",
    "--kind", "coding", "--signal", "existing_symbol",
    "--signal", "behavior_change", "--contract-evidence", CONTRACT_EVIDENCE,
    "--json",
]
STRATEGY_IDS = (
    "inspect_dependency_or_symbol_use",
    "define_contract_then_implement",
)
ACTIONS = {
    "inspect_dependency_or_symbol_use": "Inspect the existing retry API and its uses before editing.",
    "define_contract_then_implement": "Use the written retry contract as the implementation checklist.",
}
UNIT_COMMAND = "python -m unittest discover -s tests -p test_retry.py -v"
REQUIRED_COMMAND = "python -m unittest discover -s tests -v"
EXPECTED_CONTRACT_TESTS = 10
MAX_RESULT_BYTES = 256
GATE_TIMEOUT_SECONDS = 6
PREPARATION_TIMEOUT_SECONDS = 2

engine.FIXTURE = FIXTURE
engine.CHANGED_FILE = "retry.py"
engine.UNIT_COMMAND = UNIT_COMMAND
engine.CONTRACT_COMMAND = UNIT_COMMAND
engine.REQUIRED_COMMAND = REQUIRED_COMMAND
engine.CANDIDATES = ({"kind": "unit", "command": UNIT_COMMAND,
                      "relevance": 1.0, "id": "unit"},)
engine.REQUIRED = ({"command": REQUIRED_COMMAND, "id": "full"},)
engine.CHOICE_IDS = ("unit",)
engine.BASE_PROMPT = (
    "Implement the complete behavior in RETRY_CONTRACT.md for the existing "
    "retry.py module. Preserve the public Response and request_with_retry API. "
    "Read RETRY_CONTRACT.md and the existing tests before editing. Change only "
    "retry.py; do not modify or add tests. Run exactly this focused check first: "
    + UNIT_COMMAND + ". Then run the required full suite: " + REQUIRED_COMMAND
    + ". The supervisor will independently run verify_retry_contract.py on "
    "both arms; that gate is separate from these test commands. Work only in "
    "this synthetic fixture, and report no source code in your final response."
)
engine.TREATMENT_RANKING = (
    "\n\nThis is the treatment arm. Before the agent starts, the supervisor will "
    "prepare one local strategy using the exact command `jevcompass strategy "
    "choose --kind coding --signal existing_symbol --signal behavior_change "
    "--contract-evidence consistent --json`. Only a validated returned ID is "
    "provided. The route is local and has no provider usage; continue with the "
    "same required checks regardless of preparation outcome."
)
engine.OPENROUTER_KEY_HELP = ""

_original_arm = engine._run_arm
_original_event_receipts = engine._event_receipts
_expected_ids: tuple[str, ...] = ()


def frozen_files(root: Path) -> dict[str, str]:
    """Hash immutable files and record links without following their targets."""
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        relative = str(path.relative_to(root))
        if "__pycache__" in path.parts or relative == engine.CHANGED_FILE:
            continue
        if path.is_symlink():
            result[relative] = "symlink:" + str(path.readlink())
        elif path.is_file():
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _frozen_hash(files: dict[str, str]) -> str:
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _prepare(home: Path) -> tuple[dict[str, Any], None]:
    """Run only the locally resolved strategy command; never opt into a key."""
    env = engine.core._isolated_environment(
        home=home, isolated_python=home / "python", allow_openrouter_key=False
    )
    env.pop("OPENROUTER_API_KEY", None)
    try:
        completed = subprocess.run(
            STRATEGY_COMMAND, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=PREPARATION_TIMEOUT_SECONDS, cwd=ROOT, check=False,
        )
        if completed.returncode != 0 or len(completed.stdout) > engine.MAX_CHOICE_OUTPUT_BYTES:
            return {"status": "unscored", "candidate_ids": []}, None
        payload = json.loads(
            completed.stdout.decode("utf-8"), object_pairs_hook=_duplicate_rejecting_object
        )
        if (not isinstance(payload, dict) or payload.get("status") != "no-remote-choice"
                or payload.get("usage") is not None):
            return {"status": "unscored", "candidate_ids": []}, None
        choice = parse_choice_receipt(
            payload, choice_type="strategy", candidate_ids=ACTIONS
        )
        if choice["candidate_ids"] != ["inspect_dependency_or_symbol_use"]:
            return {"status": "unscored", "candidate_ids": []}, None
        return choice, None
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError, TypeError):
        return {"status": "unscored", "candidate_ids": []}, None


def _event_receipts(lines, times, started):
    lines = list(lines)
    result = _original_event_receipts(lines, times, started)
    expected_text = "JevCompass strategy receipt: " + ",".join(_expected_ids)
    acknowledgement = "not_observed"
    first_tool_seen = False
    command_order: list[str] = []
    command_counts = {"focused": 0, "required": 0}
    seen_command_ids: set[str] = set()
    for line in lines:
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        kind, message, _tool = engine.core.extract_event(event)
        if kind == "tool":
            first_tool_seen = True
        elif (kind == "assistant" and isinstance(message, str)
              and expected_text in [part.strip() for part in message.splitlines()]
              and acknowledgement == "not_observed"):
            acknowledgement = "after_first_tool" if first_tool_seen else "before_first_tool"
        if event.get("type") == "item.started":
            item = engine._item(event)
            if isinstance(item, dict):
                identifier = item.get("id")
                if (not isinstance(identifier, str) or not 0 < len(identifier) <= 128
                        or identifier in seen_command_ids):
                    continue
                seen_command_ids.add(identifier)
                command_kind = engine._test_kind(item)
                if command_kind == "unit":
                    command_counts["focused"] += 1
                    command_order.append("focused")
                elif command_kind == "required":
                    command_counts["required"] += 1
                    command_order.append("required")
    counters = engine.parse_codex_json_events(
        lines, started_at=started, event_times=times
    )
    first_tool = counters["first_tool_start"]
    result["strategy_acknowledgment"] = acknowledgement
    result["first_tool_start_ms"] = first_tool["elapsed_ms"] if first_tool else None
    result["focused_command_count"] = command_counts["focused"]
    result["required_command_count"] = command_counts["required"]
    result["test_command_order_status"] = (
        "focused_then_full"
        if command_order.count("focused") == 1
        and command_order.count("required") == 1
        and command_order.index("focused") < command_order.index("required")
        else "invalid_or_unobserved"
    )
    return result


engine._event_receipts = _event_receipts


def _parse_gate_output(stdout: bytes, returncode: int) -> dict[str, Any]:
    """Accept only the verifier's bounded schema and matching process status."""
    failure = {"status": "failed", "passed": 0, "failed": 1,
               "exit_code": 1, "tests_run": None}
    if not stdout or len(stdout) > MAX_RESULT_BYTES or stdout.count(b"\n") != 1:
        return failure
    try:
        payload = json.loads(stdout.decode("ascii"), object_pairs_hook=_duplicate_rejecting_object)
    except (UnicodeDecodeError, ValueError, TypeError):
        return failure
    if (not isinstance(payload, dict)
            or set(payload) != {"status", "passed", "failed", "exit_code"}
            or payload.get("status") not in {"passed", "failed"}
            or any(type(payload.get(key)) is not int for key in ("passed", "failed", "exit_code"))
            or payload["passed"] < 0 or payload["failed"] < 0
            or payload["exit_code"] not in {0, 1}
            or payload["exit_code"] != returncode
            or (payload["status"] == "passed") != (returncode == 0)):
        return failure
    tests_run = payload["passed"] + payload["failed"]
    if tests_run != EXPECTED_CONTRACT_TESTS:
        return failure
    if payload["status"] == "passed" and (payload["passed"] != EXPECTED_CONTRACT_TESTS
                                           or payload["failed"] != 0):
        return failure
    if payload["status"] == "failed" and payload["failed"] < 1:
        return failure
    return {**payload, "tests_run": tests_run}


def _run_independent_gate(fixture: Path, immutable_before: dict[str, str]) -> dict[str, Any]:
    changed = fixture / engine.CHANGED_FILE
    current = frozen_files(fixture)
    intact = (current == immutable_before and changed.is_file()
              and not changed.is_symlink())
    evidence: dict[str, Any] = {
        "immutable_files_preserved": intact,
        "immutable_files_sha256_before": _frozen_hash(immutable_before),
        "immutable_files_sha256_after": _frozen_hash(current),
        "contract_verifier_sha256": VERIFIER_SHA256,
        "contract_verifier_unchanged": False,
        "contract": {"status": "failed", "passed": 0, "failed": 1,
                     "exit_code": 1, "tests_run": None},
        "independent_validation_ms": None,
    }
    if not intact or hashlib.sha256(VERIFY_CONTRACT.read_bytes()).hexdigest() != VERIFIER_SHA256:
        return evidence
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [sys.executable, str(VERIFY_CONTRACT), "--fixture-dir", str(fixture)],
            cwd=ROOT, env={}, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=GATE_TIMEOUT_SECONDS, check=False,
        )
        contract = _parse_gate_output(completed.stdout, completed.returncode)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        contract = evidence["contract"]
    try:
        verifier_after = hashlib.sha256(VERIFY_CONTRACT.read_bytes()).hexdigest()
        evidence["contract_verifier_unchanged"] = verifier_after == VERIFIER_SHA256
    except OSError:
        evidence["contract_verifier_unchanged"] = False
    if not evidence["contract_verifier_unchanged"]:
        contract = evidence["contract"]
    evidence["independent_validation_ms"] = round((time.monotonic() - started) * 1000, 2)
    evidence["contract"] = contract
    return evidence


def _run_arm(**kwargs):
    global _expected_ids
    treatment = kwargs["prompt"].endswith(engine.TREATMENT_RANKING)
    prepared_at = time.monotonic()
    choice: dict[str, Any] = {"status": "not_invoked", "candidate_ids": []}
    if treatment:
        choice, _usage = _prepare(kwargs["home"])
    ids = tuple(choice.get("candidate_ids", []))
    prompt = kwargs["prompt"].removesuffix(engine.TREATMENT_RANKING)
    if ids:
        prompt += "\nOptional local JevCompass strategy advice: " + " ".join(
            ACTIONS[identifier] for identifier in ids
        ) + " Continue locally if not useful; preserve all required validation."
    prompt += "\nBefore your first tool call, report exactly: JevCompass strategy receipt: " + (
        ",".join(ids) if ids else "none"
    )
    preparation_ms = (time.monotonic() - prepared_at) * 1000 if treatment else 0.0
    immutable_before = frozen_files(kwargs["fixture"])
    _expected_ids = ids or ("none",)
    try:
        result = _original_arm(**{**kwargs, "prompt": prompt,
                                 "allow_openrouter_key": False})
    finally:
        _expected_ids = ()
    agent_ms = result.get("completion_ms")
    result["agent_completion_ms"] = agent_ms
    result["strategy_preparation_ms"] = round(preparation_ms, 2)
    result["completion_ms"] = (round(agent_ms + preparation_ms, 2)
                               if isinstance(agent_ms, (int, float)) else None)
    result["strategy"] = choice
    result["strategy_usage"] = None
    result["strategy_usage_status"] = "local_resolution_no_provider_usage"
    result["strategy_billing_estimate"] = None
    result["strategy_billing_status"] = "unknown"
    # The reused engine recognizes ParcelQuote-specific failure text. Do not
    # mislabel a retry fixture failure as a useful error timestamp.
    result["first_useful_error_ms"] = None
    if isinstance(result.get("first_observed_focused_failure_ms"), (int, float)):
        result["first_observed_focused_failure_ms"] = round(
            result["first_observed_focused_failure_ms"] + preparation_ms, 2
        )
    if isinstance(result.get("first_tool_start_ms"), (int, float)):
        result["first_tool_start_ms"] = round(
            result["first_tool_start_ms"] + preparation_ms, 2
        )

    gate = _run_independent_gate(kwargs["fixture"], immutable_before)
    result.update({
        "immutable_files_preserved": gate["immutable_files_preserved"],
        "immutable_files_sha256_before": gate["immutable_files_sha256_before"],
        "immutable_files_sha256_after": gate["immutable_files_sha256_after"],
        "contract_verifier_sha256": gate["contract_verifier_sha256"],
        "contract_verifier_unchanged": gate["contract_verifier_unchanged"],
        "independent_contract_validation": gate["contract"],
        "independent_validation_ms": gate["independent_validation_ms"],
        "independent_quality_status": (
            "frozen_checks_pass"
            if (gate["immutable_files_preserved"] and gate["contract_verifier_unchanged"]
                and gate["contract"]["status"] == "passed")
            else "failed_or_unavailable"
        ),
    })
    validation_ms = gate["independent_validation_ms"]
    arm_checks_pass = (
        result.get("cli_status") == "completed"
        and result.get("cli_exit_code") == 0
        and result.get("required_suite_exit") == 0
        and result.get("test_command_order_status") == "focused_then_full"
        and result.get("strategy_acknowledgment") == "before_first_tool"
        and result.get("focused_command_count") == 1
        and result.get("required_command_count") == 1
        and bool(result.get("focused_test_exits"))
        and all(item.get("exit_code") == 0 for item in result["focused_test_exits"])
        and result["independent_quality_status"] == "frozen_checks_pass"
    )
    result["arm_acceptance_status"] = "passed" if arm_checks_pass else "failed"
    result["validated_completion_ms"] = (
        round(result["completion_ms"] + validation_ms, 2)
        if arm_checks_pass and isinstance(result.get("completion_ms"), (int, float))
        and isinstance(validation_ms, (int, float)) else None
    )
    if not arm_checks_pass:
        result["cli_status"] = "failed"
        result["first_useful_error_ms"] = None
    result["pretask_order_status"] = "initial_prompt_before_agent_launch" if ids else "no_advice"
    return result


engine._run_arm = _run_arm

_original_private_write = engine._private_write


def _private_write(path: Path, data: bytes) -> None:
    if path.name == "receipt.json":
        receipt = json.loads(data.decode("utf-8"))
        receipt["experiment_classification"] = "local_contract_route_preparation"
        receipt["provider_efficacy_status"] = "not_measured"
        receipt["strategy_usage"] = None
        receipt["strategy_billing_estimate"] = None
        receipt["strategy_billing_status"] = "unknown"
        receipt["frozen_rubric_sha256"] = FROZEN_RUBRIC_SHA256
        receipt["contract_gate_test_count"] = EXPECTED_CONTRACT_TESTS
        receipt["contract_verifier_sha256"] = VERIFIER_SHA256
        data = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _original_private_write(path, data)


engine._private_write = _private_write
_original_run_pair = engine.run_pair


def run_pair(**kwargs):
    if kwargs.get("allow_openrouter_key"):
        raise ValueError("remote provider opt-in is not supported by this runner")
    kwargs["fixture_source"] = FIXTURE
    kwargs["allow_openrouter_key"] = False
    return _original_run_pair(**kwargs)


engine.run_pair = run_pair


def main(argv: list[str] | None = None) -> int:
    import argparse
    import shutil

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="run the two isolated Codex CLI arms")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--timeout", type=int, default=engine.DEFAULT_TIMEOUT)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("pass --live to run the two isolated Codex CLI arms")
    codex = shutil.which(args.codex)
    if not codex:
        print(json.dumps({"status": "failed", "failure": "codex_unavailable"}))
        return 2
    try:
        receipt = run_pair(
            codex=codex, model=args.model, reasoning_effort=args.reasoning_effort,
            timeout=args.timeout, seed=args.seed, output_dir=args.output_dir,
        )
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({"status": "failed", "failure": "runner_setup_failed"}))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
