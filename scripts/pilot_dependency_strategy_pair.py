#!/usr/bin/env python3
"""Run a bounded local dependency-guidance pair; this does not measure remote efficacy."""
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
FIXTURE = ROOT / "tests" / "fixtures" / "dependency_contract_review"
VERIFY_CONTRACT = ROOT / "scripts" / "verify_dependency_contract.py"
VERIFIER_SHA256 = hashlib.sha256(VERIFY_CONTRACT.read_bytes()).hexdigest()
SPEC = importlib.util.spec_from_file_location(
    "_dependency_strategy_pair_engine", ROOT / "scripts" / "pilot_test_order_pair.py"
)
engine = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(engine)

# Frozen before any future pair. This is a local evidence rubric, not a claim
# that the recommendation improves outcomes or remote-model efficacy.
FROZEN_RUBRIC = (
    "The synthetic adapter has a stable positional-or-keyword public API while "
    "the fictional dependency moved to a keyword-only method returning a "
    "context-managed entry. The written contract explicitly requires caller "
    "compatibility, exception identity, cache isolation, payload boundaries, "
    "and cleanup. The treatment is only a locally resolved inspection strategy "
    "with a fixed checklist; the independent ten-check gate determines quality."
)
FROZEN_RUBRIC_SHA256 = hashlib.sha256(FROZEN_RUBRIC.encode("utf-8")).hexdigest()

EXPECTED_STRATEGY = "inspect_dependency_or_symbol_use"
EXPECTED_RATIONALE = (
    "Inspect dependency behavior and existing symbol use before editing. "
    "Dependency migration checklist: verify the new API and signature; preserve "
    "the callers' public contract; validate exception propagation and resource ownership."
)
# Exact hashes freeze the reviewed written evidence against semantic contradictions.
API_CONTRACT_SHA256 = "87f297075113188457cc2b1b2e2e421ad562362cac91291de8acb90ac98ad742"
LEDGER_ARCHIVE_SHA256 = "2ab9eae99961c93d81a491c14612e76cbb516aceefec373664f670b71832521a"
CONTRACT_MARKERS = (
    "stable public function `ledger_adapter.load_document`",
    "load_document(client, document_key, stale_ok=False, cache=None)",
    "positional-or-keyword",
    "keyword-only",
    "shallow copy",
    "context manager",
    "cleanup occurs once",
    "same exception instance",
    "Empty document keys",
)
DEPENDENCY_MARKERS = (
    "def open_document(",
    "*, document_key:",
    "allow_stale:",
    "cache_hint:",
    "class Entry:",
    "def __enter__(",
    "def __exit__(",
    "def close(",
)

STRATEGY_COMMAND = [
    sys.executable, "-m", "jevcompass", "strategy", "choose",
    "--kind", "coding",
    "--signal", "dependency_change",
    "--signal", "behavior_change",
    "--resolved-strategy", EXPECTED_STRATEGY,
    "--json",
]
STRATEGY_IDS = (EXPECTED_STRATEGY,)
ACTIONS = {EXPECTED_STRATEGY: EXPECTED_RATIONALE}
UNIT_COMMAND = "python -m unittest discover -s tests -p test_ledger_adapter.py -v"
REQUIRED_COMMAND = "python -m unittest discover -s tests -v"
EXPECTED_CONTRACT_TESTS = 10
MAX_RESULT_BYTES = 256
GATE_TIMEOUT_SECONDS = 6
PREPARATION_TIMEOUT_SECONDS = 2

engine.FIXTURE = FIXTURE
engine.CHANGED_FILE = "ledger_adapter.py"
engine.UNIT_COMMAND = UNIT_COMMAND
engine.CONTRACT_COMMAND = UNIT_COMMAND
engine.REQUIRED_COMMAND = REQUIRED_COMMAND
engine.CANDIDATES = ({"kind": "unit", "command": UNIT_COMMAND,
                      "relevance": 1.0, "id": "unit"},)
engine.REQUIRED = ({"command": REQUIRED_COMMAND, "id": "full"},)
engine.CHOICE_IDS = ("unit",)
engine.BASE_PROMPT = (
    "Adapt only ledger_adapter.py to fully satisfy API_CONTRACT.md. Preserve "
    "the existing public load_document(client, document_key, stale_ok=False, "
    "cache=None) interface. Read API_CONTRACT.md, ledger_archive.py, and the "
    "starter tests before editing. Change only ledger_adapter.py; do not modify "
    "tests or other files. Run this focused check first: " + UNIT_COMMAND
    + ". Then run the full required suite: " + REQUIRED_COMMAND
    + ". The supervisor independently runs verify_dependency_contract.py "
    "against both arms; this gate is separate from your test commands. Work "
    "only in this synthetic fixture and report no source code in your final answer."
)
engine.TREATMENT_RANKING = (
    "\n\nThis is the treatment arm. Before launch, the supervisor verifies the "
    "written migration contract locally and runs the existing resolved strategy "
    "route with the exact local command `jevcompass strategy choose --kind coding "
    "--signal dependency_change --signal behavior_change --resolved-strategy "
    "inspect_dependency_or_symbol_use --json`. It supplies only the validated "
    "locally composed checklist. No provider request is made; run the same "
    "focused and full checks either way."
)
engine.OPENROUTER_KEY_HELP = ""

_original_arm = engine._run_arm
_original_event_receipts = engine._event_receipts
_original_private_write = engine._private_write
_original_run_pair = engine.run_pair
_expected_ids: tuple[str, ...] = ()


def frozen_files(root: Path) -> dict[str, str]:
    """Hash immutable fixture files and record links without following targets."""
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


def _written_migration_is_clear(fixture: Path) -> bool:
    """Confirm fixed public/dependency facts locally; never forward document text."""
    contract = fixture / "API_CONTRACT.md"
    dependency = fixture / "ledger_archive.py"
    try:
        if (contract.is_symlink() or dependency.is_symlink()
                or not contract.is_file() or not dependency.is_file()
                or contract.stat().st_size > 16_384
                or dependency.stat().st_size > 16_384):
            return False
        contract_bytes = contract.read_bytes()
        dependency_bytes = dependency.read_bytes()
        contract_text = contract_bytes.decode("utf-8")
        dependency_text = dependency_bytes.decode("utf-8")
    except (OSError, UnicodeError):
        return False
    return (
        hashlib.sha256(contract_bytes).hexdigest() == API_CONTRACT_SHA256
        and hashlib.sha256(dependency_bytes).hexdigest() == LEDGER_ARCHIVE_SHA256
        and all(marker in contract_text for marker in CONTRACT_MARKERS)
        and all(marker in dependency_text for marker in DEPENDENCY_MARKERS)
    )


def _prepare(home: Path, fixture: Path) -> tuple[dict[str, Any], None]:
    """Verify the written migration locally, then resolve the reviewed ID offline."""
    empty = {"status": "unscored", "candidate_ids": []}
    if not _written_migration_is_clear(fixture):
        return empty, None
    env = engine.core._isolated_environment(
        home=home, isolated_python=home / "python", allow_openrouter_key=False
    )
    env.pop("OPENROUTER_API_KEY", None)
    try:
        completed = subprocess.run(
            STRATEGY_COMMAND,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=PREPARATION_TIMEOUT_SECONDS,
            cwd=ROOT,
            check=False,
        )
        if completed.returncode != 0 or len(completed.stdout) > engine.MAX_CHOICE_OUTPUT_BYTES:
            return empty, None
        payload = json.loads(
            completed.stdout.decode("utf-8"),
            object_pairs_hook=_duplicate_rejecting_object,
        )
        if (
            not isinstance(payload, dict)
            or set(payload) != {"status", "strategies", "usage"}
            or payload.get("status") != "no-remote-choice"
            or payload.get("usage") is not None
            or not isinstance(payload.get("strategies"), list)
            or len(payload["strategies"]) != 1
        ):
            return empty, None
        row = payload["strategies"][0]
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "rationale"}
            or row.get("id") != EXPECTED_STRATEGY
            or row.get("rationale") != EXPECTED_RATIONALE
        ):
            return empty, None
        choice = parse_choice_receipt(
            payload, choice_type="strategy", candidate_ids=STRATEGY_IDS
        )
        if (
            choice["status"] != "no-remote-choice"
            or choice["candidate_ids"] != [EXPECTED_STRATEGY]
        ):
            return empty, None
        # Only the exact locally reviewed rationale is retained for prompt composition.
        choice["rationale"] = EXPECTED_RATIONALE
        return choice, None
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError, TypeError):
        return empty, None


def _event_receipts(lines, times, started):
    lines = list(lines)
    result = _original_event_receipts(lines, times, started)
    expected_text = "JevCompass dependency strategy receipt: " + ",".join(_expected_ids)
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
        elif (
            kind == "assistant"
            and isinstance(message, str)
            and expected_text in [part.strip() for part in message.splitlines()]
            and acknowledgement == "not_observed"
        ):
            acknowledgement = "after_first_tool" if first_tool_seen else "before_first_tool"
        if event.get("type") == "item.started":
            item = engine._item(event)
            if isinstance(item, dict):
                identifier = item.get("id")
                if (
                    not isinstance(identifier, str)
                    or not 0 < len(identifier) <= 128
                    or identifier in seen_command_ids
                ):
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
    result["agent_first_tool_start_ms"] = first_tool["elapsed_ms"] if first_tool else None
    result["first_tool_start_ms"] = result["agent_first_tool_start_ms"]
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
    """Accept only the verifier's exact bounded ten-check result."""
    failure = {
        "status": "failed", "passed": 0, "failed": 1,
        "exit_code": 1, "tests_run": None,
    }
    if not stdout or len(stdout) > MAX_RESULT_BYTES or stdout.count(b"\n") != 1:
        return failure
    try:
        payload = json.loads(
            stdout.decode("ascii"), object_pairs_hook=_duplicate_rejecting_object
        )
    except (UnicodeDecodeError, ValueError, TypeError):
        return failure
    if (
        not isinstance(payload, dict)
        or set(payload) != {"status", "passed", "failed", "exit_code"}
        or payload.get("status") not in {"passed", "failed"}
        or any(type(payload.get(key)) is not int for key in ("passed", "failed", "exit_code"))
        or payload["passed"] < 0
        or payload["failed"] < 0
        or payload["exit_code"] not in {0, 1}
        or payload["exit_code"] != returncode
        or (payload["status"] == "passed") != (returncode == 0)
    ):
        return failure
    tests_run = payload["passed"] + payload["failed"]
    if tests_run != EXPECTED_CONTRACT_TESTS:
        return failure
    if payload["status"] == "passed" and (
        payload["passed"] != EXPECTED_CONTRACT_TESTS or payload["failed"] != 0
    ):
        return failure
    if payload["status"] == "failed" and payload["failed"] < 1:
        return failure
    return {**payload, "tests_run": tests_run}


def _run_independent_gate(fixture: Path, immutable_before: dict[str, str]) -> dict[str, Any]:
    """Run verifier only while contract files and verifier source hashes remain frozen."""
    current_before = frozen_files(fixture)
    changed = fixture / engine.CHANGED_FILE
    intact_before = (
        current_before == immutable_before
        and changed.is_file()
        and not changed.is_symlink()
    )
    evidence: dict[str, Any] = {
        "immutable_files_preserved": intact_before,
        "immutable_files_sha256_before": _frozen_hash(immutable_before),
        "immutable_files_sha256_after": _frozen_hash(current_before),
        "contract_verifier_sha256": VERIFIER_SHA256,
        "contract_verifier_unchanged": False,
        "contract": {
            "status": "failed", "passed": 0, "failed": 1,
            "exit_code": 1, "tests_run": None,
        },
        "independent_validation_ms": None,
    }
    try:
        verifier_before = hashlib.sha256(VERIFY_CONTRACT.read_bytes()).hexdigest()
    except OSError:
        verifier_before = ""
    evidence["contract_verifier_unchanged"] = verifier_before == VERIFIER_SHA256
    if not intact_before or not evidence["contract_verifier_unchanged"]:
        return evidence

    started = time.monotonic()
    try:
        completed = subprocess.run(
            [sys.executable, str(VERIFY_CONTRACT), "--fixture-dir", str(fixture)],
            cwd=ROOT,
            env={},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=GATE_TIMEOUT_SECONDS,
            check=False,
        )
        contract = _parse_gate_output(completed.stdout, completed.returncode)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        contract = evidence["contract"]

    current_after = frozen_files(fixture)
    evidence["immutable_files_sha256_after"] = _frozen_hash(current_after)
    changed_after = fixture / engine.CHANGED_FILE
    evidence["immutable_files_preserved"] = (
        intact_before
        and current_after == immutable_before
        and changed_after.is_file()
        and not changed_after.is_symlink()
    )
    try:
        verifier_after = hashlib.sha256(VERIFY_CONTRACT.read_bytes()).hexdigest()
        evidence["contract_verifier_unchanged"] = (
            verifier_before == VERIFIER_SHA256 and verifier_after == VERIFIER_SHA256
        )
    except OSError:
        evidence["contract_verifier_unchanged"] = False
    evidence["independent_validation_ms"] = round((time.monotonic() - started) * 1000, 2)
    if not evidence["immutable_files_preserved"] or not evidence["contract_verifier_unchanged"]:
        contract = evidence["contract"]
    evidence["contract"] = contract
    return evidence


def _run_arm(**kwargs):
    global _expected_ids
    treatment = kwargs["prompt"].endswith(engine.TREATMENT_RANKING)
    prepared_at = time.monotonic()
    choice: dict[str, Any] = {"status": "not_invoked", "candidate_ids": []}
    if treatment:
        choice, _usage = _prepare(kwargs["home"], kwargs["fixture"])
    ids = tuple(choice.get("candidate_ids", []))
    prompt = kwargs["prompt"].removesuffix(engine.TREATMENT_RANKING)
    if ids:
        prompt += (
            "\nLocally resolved dependency-migration checklist: "
            + ACTIONS[ids[0]]
            + " Continue locally and preserve every required validation check."
        )
    prompt += (
        "\nBefore your first tool call, report exactly: "
        "JevCompass dependency strategy receipt: "
        + (",".join(ids) if ids else "none")
    )
    preparation_ms = (time.monotonic() - prepared_at) * 1000 if treatment else 0.0
    immutable_before = frozen_files(kwargs["fixture"])
    _expected_ids = ids or ("none",)
    try:
        result = _original_arm(**{
            **kwargs,
            "prompt": prompt,
            "allow_openrouter_key": False,
        })
    finally:
        _expected_ids = ()

    agent_ms = result.get("completion_ms")
    result["agent_completion_ms"] = agent_ms
    result["strategy_preparation_ms"] = round(preparation_ms, 2)
    result["completion_ms"] = (
        round(agent_ms + preparation_ms, 2)
        if isinstance(agent_ms, (int, float))
        else None
    )
    result["strategy"] = {
        "status": choice.get("status"),
        "candidate_ids": ids,
    }
    result["strategy_preparation_status"] = (
        "verified_local_route"
        if treatment and ids == (EXPECTED_STRATEGY,)
        else "unscored"
        if treatment
        else "not_invoked"
    )
    result["strategy_usage"] = None
    result["strategy_usage_status"] = "no_provider_request"
    result["strategy_billing_estimate"] = None
    result["strategy_billing_status"] = "unknown"
    result["billing_status"] = "unknown"
    # The shared engine recognizes task-specific useful-error markers. No
    # dependency-specific matched completion event is defined for this fixture.
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
            if (
                gate["immutable_files_preserved"]
                and gate["contract_verifier_unchanged"]
                and gate["contract"]["status"] == "passed"
                and gate["contract"]["passed"] == EXPECTED_CONTRACT_TESTS
                and gate["contract"]["failed"] == 0
                and gate["contract"]["tests_run"] == EXPECTED_CONTRACT_TESTS
            )
            else "failed_or_unavailable"
        ),
    })
    validation_ms = gate["independent_validation_ms"]
    required_status = (
        result.get("cli_status") == "completed"
        and result.get("cli_exit_code") == 0
        and result.get("required_suite_exit") == 0
        and result.get("test_command_order_status") == "focused_then_full"
        and result.get("strategy_acknowledgment") == "before_first_tool"
        and result.get("focused_command_count") == 1
        and result.get("required_command_count") == 1
        and bool(result.get("focused_test_exits"))
        and all(item.get("exit_code") == 0 for item in result["focused_test_exits"])
        and result.get("independent_quality_status") == "frozen_checks_pass"
    )
    treatment_valid = not treatment or (
        result["strategy_preparation_status"] == "verified_local_route"
    )
    arm_passed = required_status and treatment_valid
    result["arm_acceptance_status"] = "passed" if arm_passed else "failed"
    result["validated_completion_ms"] = (
        round(result["completion_ms"] + validation_ms, 2)
        if arm_passed
        and isinstance(result.get("completion_ms"), (int, float))
        and isinstance(validation_ms, (int, float))
        else None
    )
    if not arm_passed:
        result["cli_status"] = "failed"
        result["first_useful_error_ms"] = None
    result["pretask_order_status"] = (
        "initial_prompt_before_agent_launch" if ids else "no_advice"
    )
    return result


engine._run_arm = _run_arm


def _private_write(path: Path, data: bytes) -> None:
    if path.name == "receipt.json":
        receipt = json.loads(data.decode("utf-8"))
        receipt["experiment_classification"] = "local_dependency_guidance_preparation"
        receipt["provider_efficacy_status"] = "not_measured"
        receipt["strategy_usage"] = None
        receipt["strategy_usage_status"] = "no_provider_request"
        receipt["strategy_billing_estimate"] = None
        receipt["strategy_billing_status"] = "unknown"
        receipt["frozen_rubric_sha256"] = FROZEN_RUBRIC_SHA256
        receipt["contract_gate_test_count"] = EXPECTED_CONTRACT_TESTS
        receipt["contract_verifier_sha256"] = VERIFIER_SHA256
        data = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _original_private_write(path, data)


engine._private_write = _private_write


def run_pair(**kwargs):
    if kwargs.get("allow_openrouter_key"):
        raise ValueError("remote provider opt-in is not supported by this runner")
    kwargs["fixture_source"] = FIXTURE
    kwargs["allow_openrouter_key"] = False
    receipt = _original_run_pair(**kwargs)
    gates_pass = all(
        arm.get("arm_acceptance_status") == "passed"
        for arm in receipt.get("arms", {}).values()
    )
    receipt["status"] = "completed" if gates_pass and len(receipt.get("arms", {})) == 2 else "failed"
    receipt["pair_acceptance_status"] = "passed" if receipt["status"] == "completed" else "failed"
    return receipt


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
            codex=codex,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            timeout=args.timeout,
            seed=args.seed,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({"status": "failed", "failure": "runner_setup_failed"}))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
