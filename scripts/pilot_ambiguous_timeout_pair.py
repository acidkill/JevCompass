#!/usr/bin/env python3
"""Run one bounded, randomized, keyless local timeout-triage matched pair.

Both arms receive identical synthetic timeout files and the full test error.
Treatment alone receives a neutral, optional instruction to request allowlisted
triage immediately after an observed timeout, before local source inspection.
The receipt keeps task correctness separate from consultation/adoption and
contains only bounded, redacted metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable
import uuid

import pilot_cli_core as core
import pilot_test_order_pair as common
import pilot_condition_timeout_pair as timeout_fixture
from pilot_receipts import parse_choice_receipt, parse_codex_json_events

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = timeout_fixture.FIXTURE
FOCUSED_COMMAND = timeout_fixture.FOCUSED_COMMAND
REQUIRED_COMMAND = timeout_fixture.REQUIRED_COMMAND
SOURCE_FILE = timeout_fixture.SOURCE_FILE
MAX_TIMEOUT = core.MAX_TIMEOUT
DEFAULT_TIMEOUT = min(180, MAX_TIMEOUT)
MAX_TRIAGE_OUTPUT_BYTES = 64 * 1024
EXPECTED_CODEX_VERSION = timeout_fixture.EXPECTED_CODEX_VERSION
TIMEOUT_CANDIDATES = ("timeout_contention", "timeout_nonterminating")
USEFUL_FAILURE_MARKERS = timeout_fixture.USEFUL_FAILURE_MARKERS
COMMON_WORKFLOW = (
    "\n\nShared workflow for both arms: before opening, reading, searching, or "
    "editing any fixture source, test, or contract file, run the exact focused "
    f"command first: {FOCUSED_COMMAND}. Read and preserve its full error output. "
    "After the focused result, inspect local evidence, make only a contract-backed "
    "source change, rerun the focused check, then run the required full suite. "
    "Never treat a timeout or triage suggestion as success."
)
BASE_PROMPT = (
    (FIXTURE / timeout_fixture.README_FILE).read_text(encoding="utf-8")
    + COMMON_WORKFLOW
)

TRIAGE_COMMAND_PREFIX = (
    "python -m jevcompass triage --exit-code 1 --kind timeout "
    "--hypothesis timeout_contention --hypothesis timeout_nonterminating --json"
)
TREATMENT_GUIDANCE = (
    "\n\nOptional triage, if the focused test actually fails with the inbox "
    "timeout and the cause is still unclear: immediately after that completed "
    "test command, and before opening or searching fixture source, contract, or "
    "test files, you may run exactly this command:\n"
    + TRIAGE_COMMAND_PREFIX
    + "\nIt sends only the observed nonzero exit and the two allowlisted timeout "
    "hypothesis identifiers; it sends no test output, source, paths, or inferred "
    "wait-condition observations. This is nonbinding advice, not a diagnosis or "
    "a correctness result. If local evidence already resolves the cause, skip "
    "the call. In either case, inspect the full error and contract before editing, "
    "then rerun the focused and required full checks."
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_json(text: str) -> Any:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique_pairs)


def _triage_command(exit_code: int) -> tuple[str, ...]:
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code == 0:
        raise ValueError("triage requires an observed nonzero focused exit")
    parts = list(shlex.split(TRIAGE_COMMAND_PREFIX))
    parts[parts.index("1")] = str(exit_code)
    return tuple(parts)


def _is_focused(item: dict[str, Any]) -> bool:
    return common._command_argv(item) == shlex.split(FOCUSED_COMMAND)


def _is_full(item: dict[str, Any]) -> bool:
    return common._command_argv(item) == shlex.split(REQUIRED_COMMAND)


def _is_triage(item: dict[str, Any], observed_exit: int | None) -> bool:
    if observed_exit is None or observed_exit == 0:
        return False
    return common._command_argv(item) == list(_triage_command(observed_exit))


def _matches_timeout_error(output: Any) -> bool:
    return timeout_fixture._matches_useful_failure(output)


def _validated_triage(payload: Any, observed_exit: int) -> dict[str, Any]:
    empty = {"status": "unscored", "candidate_ids": [], "choice_kind": "unscored"}
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_TRIAGE_OUTPUT_BYTES:
        return empty
    try:
        value = _strict_json(payload)
    except (ValueError, TypeError, json.JSONDecodeError):
        return empty
    if not isinstance(value, dict) or value.get("executed") is not False:
        return empty
    parsed = parse_choice_receipt(
        value, choice_type="triage", candidate_ids=TIMEOUT_CANDIDATES,
    )
    if (
        parsed.get("status") not in {"remote-choice", "no-remote-choice"}
        or parsed.get("observed_exit_status") != observed_exit
        or value.get("test_failed") is not True
    ):
        return empty
    identifiers = parsed.get("candidate_ids")
    if not isinstance(identifiers, list) or len(identifiers) > len(TIMEOUT_CANDIDATES):
        return empty
    if parsed["status"] == "remote-choice" and not 1 <= len(identifiers) <= len(TIMEOUT_CANDIDATES):
        return empty
    if parsed["status"] == "no-remote-choice" and len(set(identifiers)) != len(identifiers):
        return empty
    return {
        "status": parsed["status"],
        "candidate_ids": identifiers,
        "choice_kind": "validated_remote_choice" if parsed["status"] == "remote-choice"
        else "valid_local_abstention",
    }


def _validated_usage(payload: Any) -> dict[str, int | float | None] | None:
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_TRIAGE_OUTPUT_BYTES:
        return None
    try:
        value = _strict_json(payload)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("decision_usage"), dict):
        return None
    usage = value["decision_usage"]
    inputs, outputs, cost = usage.get("input_tokens"), usage.get("output_tokens"), usage.get("cost_usd")
    if (isinstance(inputs, bool) or not isinstance(inputs, int) or not 0 <= inputs <= 10**12
            or isinstance(outputs, bool) or not isinstance(outputs, int) or not 0 <= outputs <= 10**12):
        return None
    if cost is not None and (
        isinstance(cost, bool) or not isinstance(cost, (int, float))
        or not math.isfinite(float(cost)) or not 0 <= cost <= 10**12
    ):
        return None
    return {"input_tokens": inputs, "output_tokens": outputs,
            "jev_provider_cost_usd": float(cost) if cost is not None else None}


def _elapsed_ms(times: list[float], index: int, started: float) -> float | None:
    if index >= len(times):
        return None
    elapsed = (times[index] - started) * 1000
    return round(elapsed, 2) if math.isfinite(elapsed) and elapsed >= 0 else None


def _event_receipts(
    lines: Iterable[str], event_times: list[float], started: float,
) -> dict[str, Any]:
    pending_focused: dict[str, int] = {}
    pending_full: dict[str, int] = {}
    pending_triage: dict[str, tuple[bool, bool]] = {}
    first_focus_exit: int | None = None
    first_focus_index: int | None = None
    first_error_ms: float | None = None
    useful_timeout = False
    first_tool_index: int | None = None
    first_tool_is_focused = False
    post_timeout_tool_count = 0
    triage_invocations = 0
    triage_invalid = False
    triage_phase_proven = False
    triage_completion_status = "not_invoked"
    triage_result = {"status": "unscored", "candidate_ids": [], "choice_kind": "unscored"}
    triage_usage_started: set[str] = set()
    triage_usage_completed: set[str] = set()
    triage_usages: list[dict[str, int | float | None]] = []
    focused_exits: list[int] = []
    full_exits: list[int] = []
    malformed = False

    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        item = common._item(event)
        if item is None:
            continue
        event_type = event.get("type")
        item_type = item.get("type")
        raw_id = item.get("id")
        identifier = raw_id if isinstance(raw_id, str) and 0 < len(raw_id) <= 128 else None

        if event_type == "item.started":
            tool_event = item_type != "agent_message"
            argv = common._command_argv(item)
            focused = _is_focused(item)
            full = _is_full(item)
            if tool_event and first_tool_index is None:
                first_tool_index = index
                first_tool_is_focused = focused
            if tool_event and first_focus_exit == 1 and useful_timeout:
                post_timeout_tool_count += 1
            if identifier and focused:
                if identifier in pending_focused:
                    malformed = True
                pending_focused[identifier] = index
            if identifier and full:
                if identifier in pending_full:
                    malformed = True
                pending_full[identifier] = index
            if argv and "jevcompass" in argv and "triage" in argv:
                triage_invocations += 1
                exact = _is_triage(item, first_focus_exit)
                eligible = (
                    exact and first_focus_exit == 1 and useful_timeout
                    and post_timeout_tool_count == 1
                    and first_focus_index is not None and index > first_focus_index
                )
                triage_phase_proven = triage_phase_proven or eligible
                triage_invalid = triage_invalid or not eligible
                triage_completion_status = "awaiting_result" if eligible else "invalid_or_late"
                if identifier:
                    pending_triage[identifier] = (eligible, exact)
                    if eligible and exact:
                        triage_usage_started.add(identifier)

        elif event_type == "item.completed" and identifier:
            if identifier in pending_focused:
                pending_focused.pop(identifier)
                code = item.get("exit_code")
                if isinstance(code, int) and not isinstance(code, bool):
                    focused_exits.append(code)
                    if first_focus_exit is None:
                        first_focus_exit = code
                        first_focus_index = index
                        output = item.get("aggregated_output")
                        if not isinstance(output, str):
                            output = item.get("output")
                        useful_timeout = code == 1 and _matches_timeout_error(output)
                        if useful_timeout:
                            first_error_ms = _elapsed_ms(event_times, index, started)
            if identifier in pending_full:
                pending_full.pop(identifier)
                code = item.get("exit_code")
                if isinstance(code, int) and not isinstance(code, bool):
                    full_exits.append(code)
            if identifier in pending_triage:
                eligible, exact = pending_triage.pop(identifier)
                code = item.get("exit_code")
                output = item.get("aggregated_output")
                if not isinstance(output, str):
                    output = item.get("output")
                if exact and identifier not in triage_usage_completed:
                    triage_usage_completed.add(identifier)
                    usage = _validated_usage(output)
                    if usage is not None:
                        triage_usages.append(usage)
                if eligible and exact and code == 0:
                    triage_result = _validated_triage(output, first_focus_exit or 0)
                    triage_completion_status = triage_result["choice_kind"]
                elif eligible and exact:
                    triage_completion_status = "invalid_output"

    focus_failure_observed = first_focus_exit == 1 and useful_timeout
    triage_usage_complete = (
        len(triage_usage_started) > 0
        and len(triage_usage_completed) == len(triage_usage_started)
        and len(triage_usages) == len(triage_usage_started)
    )
    if triage_invocations == 0:
        usage_status = "not_invoked"
    elif triage_invalid or not triage_phase_proven:
        usage_status = "invalid_or_unmatched"
    elif triage_usage_complete:
        usage_status = "reported"
    else:
        usage_status = "not_reported"
    triage_usage = {
        "status": usage_status,
        "invocation_count": len(triage_usage_started),
        "input_tokens": sum(u["input_tokens"] for u in triage_usages) if triage_usage_complete else None,
        "output_tokens": sum(u["output_tokens"] for u in triage_usages) if triage_usage_complete else None,
        "jev_provider_cost_usd": (
            sum(u["jev_provider_cost_usd"] for u in triage_usages)
            if triage_usage_complete and all(u["jev_provider_cost_usd"] is not None for u in triage_usages)
            else None
        ),
        "codex_billing_estimate": None,
    }
    return {
        "first_tool_was_focused": first_tool_is_focused,
        "initial_focused_exit": first_focus_exit,
        "initial_timeout_error_observed": focus_failure_observed,
        "first_useful_error_ms": first_error_ms,
        "focused_exit_codes": focused_exits,
        "focused_rerun_exit": next((code for code in focused_exits[1:] if code == 0), None),
        "full_suite_exit_codes": full_exits,
        "full_suite_exit": full_exits[-1] if full_exits else None,
        "triage_invocation_count": triage_invocations,
        "triage_invalid_invocation_observed": triage_invalid,
        "triage_phase_proven": triage_phase_proven,
        "triage_status": triage_completion_status,
        "triage_result": triage_result,
        "triage_usage": triage_usage,
        "event_sequence_valid": not malformed,
    }


def _empty_arm(status: str) -> dict[str, Any]:
    return {
        "cli_status": status, "cli_exit_code": None, "completion_ms": None,
        "initial_focused_exit": None, "initial_timeout_error_observed": False,
        "first_useful_error_ms": None, "first_tool_was_focused": False,
        "focused_exit_codes": [], "focused_rerun_exit": None,
        "full_suite_exit_codes": [], "full_suite_exit": None,
        "triage_invocation_count": 0, "triage_invalid_invocation_observed": False,
        "triage_phase_proven": False, "triage_status": "not_invoked",
        "triage_result": {"status": "unscored", "candidate_ids": [], "choice_kind": "unscored"},
        "triage_usage": {"status": "not_invoked", "invocation_count": 0,
                         "input_tokens": None, "output_tokens": None,
                         "jev_provider_cost_usd": None, "codex_billing_estimate": None},
        "event_sequence_valid": False,
    }


def _run_arm(
    *, codex: str, model: str, reasoning_effort: str, prompt: str,
    fixture: Path, home: Path, timeout: int,
) -> dict[str, Any]:
    home.joinpath(".codex").mkdir(mode=0o700, parents=True, exist_ok=True)
    env = core._isolated_environment(
        home=home, isolated_python=home / "python",
        allow_openrouter_key=False,
    )
    env.pop("OPENROUTER_API_KEY", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            common._cli_command(codex, model, reasoning_effort, prompt,
                                allow_network=False),
            cwd=fixture, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        lines, times, failure = core._collect_events(
            process, started=started, timeout=timeout, preserve_on_failure=True,
        )
    except OSError:
        return _empty_arm("failed")
    completion_ms = round((time.monotonic() - started) * 1000, 2)
    parsed = _event_receipts(lines, times, started)
    usage = parse_codex_json_events(
        lines, started_at=started, ended_at=time.monotonic(), event_times=times,
    )
    return {
        "cli_status": "completed" if failure is None and process.returncode == 0 else "failed",
        "cli_exit_code": process.returncode if failure is None else None,
        "failure": failure,
        "completion_ms": completion_ms if completion_ms <= MAX_TIMEOUT * 1000 else None,
        "codex_token_usage_status": usage["usage_status"],
        "codex_token_usage": usage["token_usage"],
        "codex_billing_estimate": None,
        **parsed,
    }


def _file_hash(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
            return None
        return _sha256(path.read_bytes())
    except OSError:
        return None


def _arm_task_correct(arm: dict[str, Any], independent: dict[str, Any], artifact_changed: bool) -> bool:
    return (
        arm.get("cli_status") == "completed"
        and arm.get("first_tool_was_focused") is True
        and arm.get("initial_focused_exit") == 1
        and arm.get("initial_timeout_error_observed") is True
        and arm.get("focused_rerun_exit") == 0
        and arm.get("full_suite_exit") == 0
        and arm.get("event_sequence_valid") is True
        and artifact_changed
        and independent.get("focused", {}).get("status") == "passed"
        and independent.get("full", {}).get("status") == "passed"
    )


def _independent_after_frozen_inputs(
    fixture: Path, home: Path, timeout: int, immutable_unchanged: bool,
) -> dict[str, Any]:
    if not immutable_unchanged:
        skipped = {
            "status": "skipped", "reason": "immutable_fixture_changed",
            "exit_code": None, "test_count": None, "elapsed_ms": None,
            "useful_failure_markers": None,
        }
        return {
            "focused": dict(skipped), "full": dict(skipped),
            "status": "failed", "reason": "immutable_fixture_changed",
        }
    return timeout_fixture._independent_validation(fixture, home, timeout)


def _verify_fixture(root: Path) -> str:
    if root.resolve() != FIXTURE.resolve():
        raise ValueError("only the reviewed timeout fixture is supported")
    files = timeout_fixture._fixture_files(root)
    observed = {p.relative_to(root).as_posix(): _sha256(p.read_bytes()) for p in files}
    if observed != timeout_fixture.REVIEWED_FIXTURE_HASHES:
        raise ValueError("reviewed timeout fixture bytes changed")
    return timeout_fixture._fixture_digest(root)


def run_pair(
    *, codex: str, model: str, reasoning_effort: str, timeout: int = DEFAULT_TIMEOUT,
    seed: int | None = None, output_dir: Path,
) -> dict[str, Any]:
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if not common.SAFE_MODEL.fullmatch(model) or not common.SAFE_ID.fullmatch(reasoning_effort):
        raise ValueError("model and reasoning effort must be simple identifiers")
    fixture_hash = _verify_fixture(FIXTURE)
    immutable_fixture_hash = timeout_fixture._fixture_digest(FIXTURE, exclude_source=True)
    if not timeout_fixture._verify_codex_version(codex):
        raise ValueError(f"Codex CLI {EXPECTED_CODEX_VERSION} is required")
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError("output path already exists; choose a fresh run directory")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(output_dir, 0o700)

    run_id = uuid.uuid4().hex
    order = ["baseline", "treatment"]
    random.Random(seed).shuffle(order)
    labels = {"baseline": "arm-a", "treatment": "arm-b"}
    if order[0] == "treatment":
        labels = {"treatment": "arm-a", "baseline": "arm-b"}
    auth_source = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"

    with tempfile.TemporaryDirectory(prefix="jev-ambiguous-timeout-pair-") as temp:
        private_root = Path(temp)
        os.chmod(private_root, 0o700)
        auth_snapshot = private_root / "auth-snapshot.json"
        try:
            auth_ok = core._copy_auth(auth_source, auth_snapshot)
        except OSError:
            auth_ok = False
        if not auth_ok:
            receipt = {"schema_version": 1, "run_id": run_id, "status": "failed",
                       "failure": "auth_unavailable", "arms": {}}
            common._private_write(output_dir / "receipt.json", (json.dumps(receipt) + "\n").encode())
            return receipt

        fixtures: dict[str, Path] = {}
        homes: dict[str, Path] = {}
        fixture_digests: dict[str, str] = {}
        prompt_hashes: dict[str, str] = {}
        prompts: dict[str, str] = {}
        arms: dict[str, dict[str, Any]] = {}
        independents: dict[str, dict[str, Any]] = {}
        artifacts: dict[str, bool] = {}
        for true_arm in ("baseline", "treatment"):
            fixtures[true_arm] = private_root / f"{true_arm}-fixture"
            homes[true_arm] = private_root / f"{true_arm}-home"
            fixture_digests[true_arm] = common._copy_identical_fixture(FIXTURE, fixtures[true_arm])
            if fixture_digests[true_arm] != fixture_hash:
                raise RuntimeError("fixture copy differs from reviewed source")
            if not core._copy_auth(auth_snapshot, homes[true_arm] / ".codex" / "auth.json"):
                raise RuntimeError("isolated authentication setup failed")

        started_pair = time.monotonic()
        for true_arm in order:
            prompt = BASE_PROMPT + (TREATMENT_GUIDANCE if true_arm == "treatment" else "")
            prompts[true_arm] = prompt
            prompt_hashes[true_arm] = _sha256(prompt.encode("utf-8"))
            arms[labels[true_arm]] = _run_arm(
                codex=codex, model=model, reasoning_effort=reasoning_effort,
                prompt=prompt, fixture=fixtures[true_arm], home=homes[true_arm],
                timeout=timeout,
            )
            final_hash = _file_hash(fixtures[true_arm] / SOURCE_FILE)
            initial_hash = _file_hash(FIXTURE / SOURCE_FILE)
            artifact_changed = final_hash is not None and initial_hash is not None and final_hash != initial_hash
            arms[labels[true_arm]]["source_artifact_sha256"] = final_hash
            arms[labels[true_arm]]["source_artifact_changed"] = artifact_changed
            arms[labels[true_arm]]["immutable_fixture_unchanged"] = (
                timeout_fixture._fixture_digest(fixtures[true_arm], exclude_source=True)
                == immutable_fixture_hash
            )
            artifacts[labels[true_arm]] = artifact_changed

        for true_arm in ("baseline", "treatment"):
            label = labels[true_arm]
            arm_fixture = fixtures[true_arm]
            validation_home = homes[true_arm] / "independent"
            independents[label] = _independent_after_frozen_inputs(
                arm_fixture, validation_home, min(timeout, 30),
                arms[label].get("immutable_fixture_unchanged") is True,
            )
            arms[label]["immutable_fixture_unchanged"] = (
                arms[label]["immutable_fixture_unchanged"]
                and timeout_fixture._fixture_digest(arm_fixture, exclude_source=True)
                == immutable_fixture_hash
            )

    task_correctness = {
        label: "passed" if _arm_task_correct(
            arms[label], independents[label], artifacts[label],
        ) and arms[label].get("immutable_fixture_unchanged") else "failed"
        for label in ("arm-a", "arm-b")
    }
    mapping = {
        "run_id": run_id,
        "arm-a": "treatment" if labels["treatment"] == "arm-a" else "baseline",
        "arm-b": "treatment" if labels["treatment"] == "arm-b" else "baseline",
    }
    common._private_write(output_dir / "arm-map.json", (json.dumps(mapping, sort_keys=True)+"\n").encode())
    treatment_label = labels["treatment"]
    treatment_arm = arms[treatment_label]
    if treatment_arm["triage_invocation_count"] == 0:
        protocol_status = "not_invoked_or_skipped"
    elif treatment_arm["triage_phase_proven"] and not treatment_arm["triage_invalid_invocation_observed"]:
        protocol_status = treatment_arm["triage_status"]
    else:
        protocol_status = "invalid_or_late"
    receipt = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "completed" if all(v == "passed" for v in task_correctness.values()) else "failed",
        "task_correctness": task_correctness,
        "advice_protocol_status": protocol_status,
        "advice_adoption_status": "not_scored_nonbinding",
        "advice_effect_scope": "prospective_optional_local_guidance_only",
        "triage_execution_profile": "keyless_local_fallback_only",
        "remote_advice_status": "not_attempted_keyless_profile",
        "agent_api_key_exposed": False,
        "agent_network_access": False,
        "pair_order": [labels[arm] for arm in order],
        "timeout_seconds_per_arm": timeout,
        "fixture_sha256": fixture_hash,
        "immutable_fixture_sha256": immutable_fixture_hash,
        "fixture_parity": fixture_digests["baseline"] == fixture_digests["treatment"],
        "prompt_hashes": {
            "baseline": prompt_hashes["baseline"],
            "treatment": prompt_hashes["treatment"],
            "shared_workflow_sha256": _sha256(BASE_PROMPT.encode("utf-8")),
            "same_common_task": (
                prompts["baseline"] == BASE_PROMPT
                and prompts["treatment"].startswith(BASE_PROMPT)
            ),
        },
        "arms": arms,
        "independent_validation": independents,
        "pair_wall_ms": round((time.monotonic() - started_pair) * 1000, 2),
        "provider_cost_available": False,
        "billing_estimate": None,
        "receipt_scope": "redacted metadata only; no prompts, source, paths, tool output, or credentials",
    }
    common._private_write(output_dir / "receipt.json", (json.dumps(receipt, sort_keys=True, indent=2)+"\n").encode())
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="authorize execution of Codex CLI arms")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("pass --live to run model-backed CLI arms")
    codex = shutil.which(args.codex)
    if not codex:
        print(json.dumps({"status": "failed", "failure": "codex_unavailable"}))
        return 2
    try:
        receipt = run_pair(
            codex=codex, model=args.model, reasoning_effort=args.reasoning_effort,
            timeout=args.timeout, seed=args.seed, output_dir=args.output_dir,
        )
    except (OSError, ValueError, RuntimeError, TypeError):
        print(json.dumps({"status": "failed", "failure": "runner_setup_failed"}))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
