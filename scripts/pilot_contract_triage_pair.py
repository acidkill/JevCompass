#!/usr/bin/env python3
"""Run a bounded matched CLI pair on the synthetic contract ambiguity fixture.

The baseline uses the fixture README unchanged. The treatment adds a request to
inspect the local contract, legacy golden, implementation, and test before
classifying confirmed underspecification with local enum-only triage. Raw events,
prompts, diagnostics, and final answers stay in memory or private blind artifacts.
A completed run is not a passing test suite or an efficacy result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
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
from pilot_receipts import (
    build_agent_measurement_receipt, parse_codex_json_events, parse_choice_receipt,
)
import pilot_triage_pair as triage_common


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ambiguous_contract_triage"
SOURCE_FILE = "contractquote/totals.py"
TEST_FILE = "tests/test_invoice.py"
MAX_ARTIFACT_BYTES = 64 * 1024
MAX_ANSWER_BYTES = 64 * 1024
MAX_TIMEOUT = core.MAX_TIMEOUT
DEFAULT_TIMEOUT = min(180, MAX_TIMEOUT)
EVENT_LIMIT = core.MAX_EVENT_BYTES
CODEX_VERSION = "0.157.0"

FOCUSED_COMMAND = "python -m unittest discover -s tests -p 'test_invoice.py' -v"
REQUIRED_COMMAND = "python -m unittest discover -s tests -v"
TRIAGE_PREFIX = ("python", "-m", "jevcompass", "triage", "--exit-code")
TRIAGE_SUFFIX = (
    "--kind", "assertion",
    "--hypothesis", "assertion_expectation_drift",
    "--hypothesis", "assertion_behavior_regression",
    "--hypothesis", "confirm_behavior_contract",
    "--assertion-observation", "contract_underspecified",
    "--json",
)
TRIAGE_IDS = ("confirm_behavior_contract",)
ADVICE_POLICIES = ("legacy-required-step", "nonbinding")
WORKFLOW_ACK_LINE = "JevCompass triage workflow instructions received."
TRIAGE_RESULT_ACK_LINE = (
    "JevCompass local triage result received: confirm_behavior_contract."
)
EVIDENCE_FILES = {
    "contract": "INVOICE_CONTRACT.md",
    "legacy": "legacy_golden.json",
    "implementation": "contractquote/totals.py",
    "test": "tests/test_invoice.py",
}

BASE_PROMPT = (FIXTURE / "README.md").read_text(encoding="utf-8")
TREATMENT_PROMPT = (
    BASE_PROMPT
    + "\n\nAfter the initial focused test has completed, inspect the local "
    "contract, legacy golden, implementation, and focused test. Compare the "
    "requirements with the observed behavior and determine whether the local "
    "evidence establishes the intended behavior. Do not infer policy from "
    "current code or a legacy value alone. Only if your inspection shows that "
    "the available contract evidence does not establish expected behavior, "
    "run this exact local enum-only classification using the observed focused "
    "exit code: "
    "python -m jevcompass triage --exit-code 1 --kind assertion "
    "--hypothesis assertion_expectation_drift "
    "--hypothesis assertion_behavior_regression "
    "--hypothesis confirm_behavior_contract "
    "--assertion-observation contract_underspecified --json. "
    "Do not pass source text, values, paths, or diagnostics to the command. "
    "Preserve the original failed test status, make no unsupported source or "
    "test changes, and report unresolved policy questions without guessing."
)
NONBINDING_TREATMENT_PROMPT = (
    BASE_PROMPT
    + "\n\nBefore using any tool, make your first assistant message contain this "
    + "exact line: " + WORKFLOW_ACK_LINE
    + " If you request triage, after its result and before using another tool, "
    + "make your next assistant message contain this exact line: "
    + TRIAGE_RESULT_ACK_LINE
    + "\n\nAfter the initial focused test has completed, inspect the local "
    + "contract, legacy golden, implementation, and focused test. Compare the "
    + "requirements with the observed behavior and determine whether the local "
    + "evidence establishes the intended behavior. Do not infer policy from "
    + "current code or a legacy value alone. If the available contract evidence "
    + "does not establish expected behavior, you may request this exact local "
    + "enum-only triage using the observed focused exit code: "
    + "python -m jevcompass triage --exit-code 1 --kind assertion "
    + "--hypothesis assertion_expectation_drift "
    + "--hypothesis assertion_behavior_regression "
    + "--hypothesis confirm_behavior_contract "
    + "--assertion-observation contract_underspecified --json. "
    + "The request is optional; a local no-remote-choice result or not "
    + "requesting triage does not require a source or test change and does not "
    + "change the correctness criteria. Do not pass source text, values, paths, "
    + "or diagnostics to the command. Preserve the original failed test status, "
    + "make no unsupported source or test changes, and report unresolved policy "
    + "questions without guessing."
)
SAFE_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
SAFE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")
def _matches_useful_failure(output: Any) -> bool:
    return (
        isinstance(output, str)
        and "FAIL: test_invoice_total_matches_legacy_golden" in output
        and "AssertionError: Decimal('0.02') != Decimal('0.01')" in output
    )
_REQUIRED_EVIDENCE = frozenset(EVIDENCE_FILES)


def _item(event: dict[str, Any]) -> dict[str, Any] | None:
    return common._item(event)


def _focused(item: dict[str, Any]) -> bool:
    return common._command_argv(item) == shlex.split(FOCUSED_COMMAND)


def _full(item: dict[str, Any]) -> bool:
    return common._command_argv(item) == shlex.split(REQUIRED_COMMAND)


def _triage_argv(exit_code: int) -> list[str]:
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code == 0:
        raise ValueError("triage requires an observed nonzero focused exit")
    return [*TRIAGE_PREFIX, str(exit_code), *TRIAGE_SUFFIX]


def _is_triage(item: dict[str, Any], exit_code: int | None) -> bool:
    return exit_code is not None and exit_code != 0 and (
        common._command_argv(item) == _triage_argv(exit_code)
    )


def _evidence_kinds(item: dict[str, Any]) -> tuple[str, ...]:
    argv = common._command_argv(item) or []
    joined = " ".join(argv)
    return tuple(
        kind for kind, relative in EVIDENCE_FILES.items()
        if relative in joined
    )


def _evidence_confirms(kind: str, output: Any) -> bool:
    if not isinstance(output, str) or len(output.encode("utf-8")) > MAX_ANSWER_BYTES:
        return False
    lowered = output.lower()
    if kind == "contract":
        return "half-up" in lowered and "cent" in lowered
    if kind == "legacy":
        return "0.005" in lowered and "0.01" in lowered
    if kind == "implementation":
        return "rounded_line_amounts" in lowered and "sum(" in lowered
    if kind == "test":
        return "invoice_total" in lowered and "0.01" in lowered
    return False


def _strict_json(text: str) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs)


def _validated_triage(payload: Any, focused_exit: int) -> dict[str, Any]:
    if (not isinstance(payload, str)
            or len(payload.encode("utf-8")) > triage_common.MAX_TRIAGE_OUTPUT_BYTES):
        return {"status": "unscored", "candidate_ids": []}
    try:
        value = _strict_json(payload)
    except (ValueError, TypeError, json.JSONDecodeError):
        return {"status": "unscored", "candidate_ids": []}
    if not isinstance(value, dict) or value.get("executed") is not False:
        return {"status": "unscored", "candidate_ids": []}
    parsed = parse_choice_receipt(
        value, choice_type="triage", candidate_ids=TRIAGE_IDS,
    )
    if (
        parsed.get("status") != "no-remote-choice"
        or parsed.get("candidate_ids") != ["confirm_behavior_contract"]
        or parsed.get("observed_exit_status") != focused_exit
        or value.get("test_failed") is not True
    ):
        return {"status": "unscored", "candidate_ids": []}
    return parsed


def _answer_text(item: dict[str, Any]) -> str | None:
    if item.get("type") not in {"agent_message", "assistant_message", "message"}:
        return None
    value = item.get("text")
    if not isinstance(value, str) and isinstance(item.get("content"), list):
        value = "\n".join(
            part.get("text", "") for part in item["content"]
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return value if isinstance(value, str) else None


def _event_receipts(
    lines: Iterable[str], event_times: list[float], started: float,
    *, advice_policy: str = "legacy-required-step",
) -> tuple[dict[str, Any], str | None]:
    lines = list(lines)
    pending: dict[str, tuple[str, str | None]] = {}
    evidence: set[str] = set()
    focused_exits: list[int] = []
    full_exits: list[int] = []
    useful_failure_ms: float | None = None
    useful_failure_observed = False
    triage_seen = False
    triage_after_evidence = False
    triage_invalid = False
    triage_exit: int | None = None
    triage_choice = {"status": "unscored", "candidate_ids": []}
    triage_output_status = "not_invoked"
    triage_usage = None
    last_answer: str | None = None
    evidence_before_triage = False
    workflow_acknowledgment = "not_observed"
    triage_result_acknowledgment = "not_applicable"
    first_assistant_seen = False
    first_tool_seen = False
    triage_result_index: int | None = None
    first_post_triage_assistant_seen = False
    first_post_triage_tool_seen = False

    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        if advice_policy == "nonbinding":
            event_kind, message, _tool = core.extract_event(event)
            if event_kind == "tool" and not first_tool_seen:
                first_tool_seen = True
            elif event_kind == "assistant" and not first_assistant_seen:
                first_assistant_seen = True
                has_ack = (
                    isinstance(message, str)
                    and WORKFLOW_ACK_LINE in [line.strip() for line in message.splitlines()]
                )
                if has_ack:
                    workflow_acknowledgment = (
                        "after_first_tool" if first_tool_seen else "before_first_tool"
                    )
                else:
                    workflow_acknowledgment = "invalid"
            if triage_result_index is not None:
                if event_kind == "tool" and not first_post_triage_tool_seen:
                    first_post_triage_tool_seen = True
                elif event_kind == "assistant" and not first_post_triage_assistant_seen:
                    first_post_triage_assistant_seen = True
                    has_result_ack = (
                        isinstance(message, str)
                        and TRIAGE_RESULT_ACK_LINE in [line.strip() for line in message.splitlines()]
                    )
                    if has_result_ack:
                        triage_result_acknowledgment = (
                            "after_next_tool" if first_post_triage_tool_seen
                            else "before_next_tool"
                        )
                    else:
                        triage_result_acknowledgment = "invalid"
        item = _item(event)
        if item is None:
            continue
        event_type = event.get("type")
        raw_id = item.get("id")
        event_id = raw_id if isinstance(raw_id, str) and 0 < len(raw_id) <= 128 else None
        if event_type == "item.completed":
            answer = _answer_text(item)
            if answer is not None:
                last_answer = answer
        if not event_id:
            continue
        if event_type == "item.started":
            kind = None
            if _focused(item):
                kind = "focused"
            elif _full(item):
                kind = "full"
            else:
                evidence_kinds = _evidence_kinds(item)
                if evidence_kinds:
                    kind = "evidence:" + ",".join(evidence_kinds)
            if kind:
                pending[event_id] = (kind, None)
            argv = common._command_argv(item)
            if argv and "jevcompass" in argv:
                triage_seen = True
                exact = _is_triage(item, focused_exits[0] if focused_exits else None)
                evidence_before_triage = _REQUIRED_EVIDENCE.issubset(evidence)
                accepted = (
                    exact and bool(focused_exits) and focused_exits[0] == 1
                    and useful_failure_observed and evidence_before_triage
                )
                triage_after_evidence = triage_after_evidence or accepted
                triage_invalid = triage_invalid or not accepted
                pending[event_id] = ("triage-accepted" if accepted else "triage-invalid", None)
                if not accepted:
                    triage_output_status = "out_of_order_or_unverified"
                else:
                    triage_output_status = "captured"
        elif event_type == "item.completed" and event_id in pending:
            kind, _ = pending.pop(event_id)
            code = item.get("exit_code")
            if kind == "focused" and isinstance(code, int) and not isinstance(code, bool):
                focused_exits.append(code)
                output = item.get("aggregated_output")
                if not isinstance(output, str):
                    output = item.get("output")
                if (len(focused_exits) == 1 and code == 1
                        and _matches_useful_failure(output)):
                    useful_failure_observed = True
                    observed = event_times[index] if index < len(event_times) else None
                    if observed is not None:
                        useful_failure_ms = round(max(0.0, (observed - started) * 1000), 2)
            elif kind == "full" and isinstance(code, int) and not isinstance(code, bool):
                full_exits.append(code)
            elif (kind.startswith("evidence:") and isinstance(code, int)
                  and not isinstance(code, bool) and code == 0
                  and bool(focused_exits) and focused_exits[0] == 1):
                output = item.get("aggregated_output")
                if not isinstance(output, str):
                    output = item.get("output")
                for evidence_kind in kind.split(":", 1)[1].split(","):
                    if _evidence_confirms(evidence_kind, output):
                        evidence.add(evidence_kind)
            elif kind in {"triage-accepted", "triage-invalid"}:
                triage_exit = code if isinstance(code, int) and not isinstance(code, bool) else None
                output = item.get("aggregated_output")
                if not isinstance(output, str):
                    output = item.get("output")
                if kind == "triage-accepted":
                    usage_fn = getattr(triage_common, "_validated_decision_usage", None)
                    if callable(usage_fn):
                        triage_usage = usage_fn(output)
                    if code == 0:
                        triage_choice = _validated_triage(output, focused_exits[0])
                        triage_output_status = (
                            "valid_local_step"
                            if triage_choice.get("candidate_ids") == ["confirm_behavior_contract"]
                            else "invalid_output"
                        )
                        if triage_output_status == "valid_local_step":
                            triage_result_index = index
                else:
                    triage_output_status = "out_of_order_or_unverified"
    token_counts = parse_codex_json_events(
        lines, started_at=started, ended_at=(event_times[-1] if event_times else started),
        event_times=event_times,
    )
    result = {
        "initial_focused_exit": focused_exits[0] if focused_exits else None,
        "focused_exit_codes": focused_exits,
        "full_suite_exit": full_exits[-1] if full_exits else None,
        "full_suite_invocation_observed": bool(full_exits),
        "first_useful_failure_observed": useful_failure_observed,
        "first_useful_failure_ms": useful_failure_ms,
        "policy_check_timing_ms": None,
        "policy_check_timing_status": "unscored",
        "evidence_categories_verified": sorted(evidence),
        "evidence_complete_before_triage": evidence_before_triage and triage_after_evidence,
        "triage_invocation_observed": triage_seen,
        "triage_after_evidence": triage_after_evidence,
        "triage_invalid_invocation_observed": triage_invalid,
        "triage_exit_code": triage_exit,
        "triage_output_status": triage_output_status,
        "triage": triage_choice,
        "triage_usage_status": "complete" if triage_usage is not None else (
            "not_invoked" if not triage_seen else "unscored"
        ),
        "triage_usage": triage_usage,
        "codex_token_usage_status": token_counts["usage_status"],
        "codex_token_usage": token_counts["token_usage"],
        "codex_billing_estimate": None,
        "event_count": len(list(lines)) if isinstance(lines, list) else None,
    }
    if advice_policy == "nonbinding":
        result["workflow_acknowledgment"] = workflow_acknowledgment
        result["triage_result_acknowledgment"] = triage_result_acknowledgment
    return result, last_answer


def _empty_arm(failure: str) -> dict[str, Any]:
    return {
        "cli_status": "failed", "failure": failure, "cli_exit_code": None,
        "completion_ms": None, "event_count": 0,
        "agent_measurement": None,
        "initial_focused_exit": None, "focused_exit_codes": [],
        "full_suite_exit": None, "full_suite_invocation_observed": False,
        "first_useful_failure_observed": False, "first_useful_failure_ms": None,
        "policy_check_timing_ms": None, "policy_check_timing_status": "unscored",
        "evidence_categories_verified": [], "evidence_complete_before_triage": False,
        "triage_invocation_observed": False, "triage_after_evidence": False,
        "triage_invalid_invocation_observed": False, "triage_exit_code": None,
        "triage_output_status": "not_invoked",
        "triage": {"status": "unscored", "candidate_ids": []},
        "triage_usage_status": "not_invoked", "triage_usage": None,
        "codex_token_usage_status": "unscored", "codex_token_usage": None,
        "codex_billing_estimate": None,
    }


def _run_arm(
    *, codex: str, model: str, reasoning_effort: str, prompt: str,
    fixture: Path, home: Path, timeout: int, treatment: bool,
    measurement_path: Path | None = None,
    advice_policy: str = "legacy-required-step",
) -> tuple[dict[str, Any], str | None]:
    (home / ".codex").mkdir(mode=0o700, parents=True, exist_ok=True)
    measurement_path = measurement_path or (home / ".codex" / "agent-measurement.json")
    env = core._isolated_environment(
        home=home, isolated_python=home / "python", allow_openrouter_key=False,
    )
    env.pop("OPENROUTER_API_KEY", None)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            common._cli_command(codex, model, reasoning_effort, prompt, allow_network=False),
            cwd=fixture, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        lines, times, failure = core._collect_events(
            process, started=started, timeout=timeout, preserve_on_failure=True,
        )
    except OSError:
        return _empty_arm("codex_unavailable"), None

    ended = time.monotonic()
    wall_ms = round((ended - started) * 1000, 2)
    measurement = build_agent_measurement_receipt(
        lines, times, started, ended, process.returncode, failure,
    )
    # Write this bounded, redacted record before the optional task-specific
    # event interpretation so a parser error cannot erase early measurements.
    common._private_write(
        measurement_path,
        (json.dumps(measurement, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
    )

    if failure is not None:
        failed = _empty_arm(failure)
        failed["completion_ms"] = wall_ms if wall_ms <= MAX_TIMEOUT * 1000 else None
        failed["cli_exit_code"] = process.returncode
        failed["agent_measurement"] = measurement
        failed["event_count"] = len(lines)
        return failed, None

    observed, answer = _event_receipts(
        lines, times, started, advice_policy=advice_policy,
    )
    result = {
        "cli_status": "completed" if process.returncode == 0 else "failed",
        "failure": None if process.returncode == 0 else "cli_exit_nonzero",
        "cli_exit_code": process.returncode,
        "completion_ms": wall_ms if wall_ms <= MAX_TIMEOUT * 1000 else None,
        "agent_measurement": measurement,
        **observed,
    }
    return result, answer


def _fixture_digest(root: Path) -> str | None:
    """Hash fixture files while ignoring generated Python bytecode."""
    digest = hashlib.sha256()
    try:
        for path in sorted(root.rglob("*")):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            if path.is_symlink():
                digest.update(b"symlink")
            elif path.is_file():
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
                    return None
                content = path.read_bytes()
                digest.update(len(content).to_bytes(8, "big"))
                digest.update(content)
            elif path.is_dir():
                digest.update(b"directory")
            else:
                digest.update(b"other")
    except OSError:
        return None
    return digest.hexdigest()


def _safe_artifact(path: Path) -> bytes | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
            return None
        value = path.read_bytes()
        return value if len(value) <= MAX_ARTIFACT_BYTES else None
    except OSError:
        return None


def _verify_codex_version(codex: str) -> bool:
    try:
        completed = subprocess.run(
            [codex, "--version"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=5, check=False, env=core._isolated_environment(
                home=Path(tempfile.gettempdir()) / "jev-version-check",
                isolated_python=Path(tempfile.gettempdir()) / "jev-python",
                allow_openrouter_key=False,
            ),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return (
        completed.returncode == 0
        and completed.stdout.strip().endswith(CODEX_VERSION)
    )


def run_pair(
    *, codex: str, model: str, reasoning_effort: str,
    timeout: int = DEFAULT_TIMEOUT, seed: int | None = None,
    output_dir: Path, fixture_source: Path = FIXTURE,
    advice_policy: str = "legacy-required-step",
) -> dict[str, Any]:
    """Run both local-only CLI arms and save private blind artifacts/receipts."""
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if not fixture_source.is_dir():
        raise FileNotFoundError("synthetic contract fixture is unavailable")
    if (not SAFE_MODEL.fullmatch(model) or not SAFE_ID.fullmatch(reasoning_effort)):
        raise ValueError("model and reasoning effort must be simple identifiers")
    if not isinstance(advice_policy, str) or advice_policy not in ADVICE_POLICIES:
        raise ValueError("invalid advice policy")
    if not _verify_codex_version(codex):
        raise ValueError("Codex CLI 0.157.0 is required")

    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValueError("output directory must be a real directory")
    os.chmod(output_dir, 0o700)
    run_id = uuid.uuid4().hex
    rng = random.Random(seed)
    order = ["baseline", "treatment"]
    rng.shuffle(order)
    labels = {name: f"arm-{'a' if order[0] == name else 'b'}" for name in order}
    source_auth = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"

    with tempfile.TemporaryDirectory(prefix="jev-contract-pair-") as temp:
        private_root = Path(temp)
        os.chmod(private_root, 0o700)
        auth_snapshot = private_root / "auth-snapshot.json"
        try:
            auth_ok = core._copy_auth(source_auth, auth_snapshot)
        except OSError:
            auth_ok = False
        if not auth_ok:
            receipt = {
                "schema_version": 1, "run_id": run_id, "status": "failed",
                "failure": "auth_unavailable", "arms": {},
            }
            common._private_write(output_dir / "receipt.json",
                                  (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode())
            return receipt
        fixtures: dict[str, Path] = {}
        homes: dict[str, Path] = {}
        digests: dict[str, str] = {}
        initial_sources = {
            name: _safe_artifact(fixture_source / name)
            for name in (SOURCE_FILE, TEST_FILE)
        }
        initial_fixture_digest = _fixture_digest(fixture_source)
        for true_arm in ("baseline", "treatment"):
            fixtures[true_arm] = private_root / f"{true_arm}-fixture"
            homes[true_arm] = private_root / f"{true_arm}-home"
            digests[true_arm] = common._copy_identical_fixture(
                fixture_source, fixtures[true_arm],
            )
            if not core._copy_auth(auth_snapshot, homes[true_arm] / ".codex" / "auth.json"):
                receipt = {
                    "schema_version": 1, "run_id": run_id, "status": "failed",
                    "failure": "auth_setup_failed", "arms": {},
                }
                common._private_write(output_dir / "receipt.json",
                                      (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode())
                return receipt
        if digests["baseline"] != digests["treatment"]:
            raise RuntimeError("fixture parity verification failed")

        arms: dict[str, dict[str, Any]] = {}
        answers: dict[str, str | None] = {}
        for true_arm in order:
            label = labels[true_arm]
            prompt = BASE_PROMPT if true_arm == "baseline" else (
                NONBINDING_TREATMENT_PROMPT if advice_policy == "nonbinding"
                else TREATMENT_PROMPT
            )
            arms[label], answers[label] = _run_arm(
                codex=codex, model=model, reasoning_effort=reasoning_effort,
                prompt=prompt, fixture=fixtures[true_arm], home=homes[true_arm],
                timeout=timeout, treatment=true_arm == "treatment",
                advice_policy=(advice_policy if true_arm == "treatment"
                               else "legacy-required-step"),
                measurement_path=output_dir / f"{label}-agent-measurement.json",
            )
            arms[label]["fixture_sha256_before"] = digests[true_arm]

        source_unchanged: dict[str, bool] = {}
        tests_unchanged: dict[str, bool] = {}
        fixture_unchanged: dict[str, bool] = {}
        for true_arm in ("baseline", "treatment"):
            label = labels[true_arm]
            for relative, target in ((SOURCE_FILE, source_unchanged), (TEST_FILE, tests_unchanged)):
                final = _safe_artifact(fixtures[true_arm] / relative)
                original = initial_sources.get(relative)
                target[label] = final is not None and original is not None and final == original
            fixture_unchanged[label] = (
                initial_fixture_digest is not None
                and _fixture_digest(fixtures[true_arm]) == initial_fixture_digest
            )
            source_bytes = _safe_artifact(fixtures[true_arm] / SOURCE_FILE)
            if source_bytes is not None:
                common._private_write(output_dir / f"{label}-source.py", source_bytes)
            answer = answers.get(label)
            if isinstance(answer, str) and len(answer.encode("utf-8")) <= MAX_ANSWER_BYTES:
                common._private_write(
                    output_dir / f"{label}-final.txt", answer.encode("utf-8"),
                )

        mapping = {
            "run_id": run_id,
            "arm-a": "treatment" if labels["treatment"] == "arm-a" else "baseline",
            "arm-b": "treatment" if labels["treatment"] == "arm-b" else "baseline",
        }
        common._private_write(output_dir / "arm-map.json",
                              (json.dumps(mapping, sort_keys=True) + "\n").encode())

        protocol_complete = all(
            arms.get(label, {}).get("cli_status") == "completed"
            and arms[label].get("initial_focused_exit") == 1
            and arms[label].get("full_suite_invocation_observed") is True
            and arms[label].get("full_suite_exit") == 1
            and source_unchanged.get(label) is True
            and tests_unchanged.get(label) is True
            and fixture_unchanged.get(label) is True
            for label in ("arm-a", "arm-b")
        )
        baseline_label = "arm-a" if mapping["arm-a"] == "baseline" else "arm-b"
        baseline = arms.get(baseline_label, {})
        baseline_gate = (
            baseline.get("first_useful_failure_observed") is True
            and baseline.get("triage_invocation_observed") is False
        )
        treatment_label = "arm-a" if mapping["arm-a"] == "treatment" else "arm-b"
        treatment = arms.get(treatment_label, {})
        treatment_gate = (
            treatment.get("first_useful_failure_observed") is True
            and treatment.get("evidence_complete_before_triage") is True
            and treatment.get("triage_after_evidence") is True
            and treatment.get("triage_invalid_invocation_observed") is False
            and treatment.get("triage_exit_code") == 0
            and treatment.get("triage_output_status") == "valid_local_step"
        )
        if advice_policy == "nonbinding":
            workflow_acknowledged = (
                treatment.get("workflow_acknowledgment") == "before_first_tool"
            )
            verified_evidence = set(treatment.get("evidence_categories_verified", [])) == set(
                _REQUIRED_EVIDENCE
            )
            valid_triage = (
                treatment.get("triage_after_evidence") is True
                and treatment.get("triage_invalid_invocation_observed") is False
                and treatment.get("triage_exit_code") == 0
                and treatment.get("triage_output_status") == "valid_local_step"
                and treatment.get("triage_result_acknowledgment") == "before_next_tool"
            )
            valid_abstention = (
                treatment.get("cli_status") == "completed"
                and treatment.get("first_useful_failure_observed") is True
                and verified_evidence
                and treatment.get("triage_invocation_observed") is False
                and treatment.get("triage_invalid_invocation_observed") is False
            )
            treatment_gate = (
                treatment.get("first_useful_failure_observed") is True
                and verified_evidence and workflow_acknowledged
                and (valid_triage or valid_abstention)
            )
            task_correctness = bool(
                protocol_complete and baseline_gate
                and treatment.get("first_useful_failure_observed") is True
                and verified_evidence
            )
            delivery_ok = workflow_acknowledged and (valid_triage or valid_abstention)
            if not workflow_acknowledged or not (valid_triage or valid_abstention):
                delivery_status = "delivery_failed"
            elif valid_abstention:
                delivery_status = "abstained"
            else:
                delivery_status = "advice_delivered"
            adoption_status = (
                "unscored" if valid_triage else "not_applicable"
                if valid_abstention else "unscored"
            )
            effect_scope = (
                "local_triage_advice" if valid_triage else "local_abstention"
                if valid_abstention else "unscored"
            )
        else:
            task_correctness = None
            delivery_ok = True
            delivery_status = None
            adoption_status = None
            effect_scope = None
        pair_passed = protocol_complete and treatment_gate and baseline_gate and delivery_ok
        receipt = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "completed" if pair_passed else "incomplete",
            "run_status_scope": "protocol_observation_only_not_test_success_or_efficacy",
            "codex_cli_version_required": CODEX_VERSION,
            "pair_order": [labels[name] for name in order],
            "timeout_seconds_per_arm": timeout,
            "event_limit_bytes": EVENT_LIMIT,
            "fixture_parity_sha256": digests["baseline"],
            "randomization_seed": seed,
            "original_failed_exit_preserved": all(
                arms.get(label, {}).get("initial_focused_exit") == 1
                for label in ("arm-a", "arm-b")
            ),
            "source_unchanged": source_unchanged,
            "tests_unchanged": tests_unchanged,
            "fixture_unchanged_excluding_bytecode": fixture_unchanged,
            "baseline_gate_observed": baseline_gate,
            "blind_artifacts": {
                label: {
                    "source_captured": (output_dir / f"{label}-source.py").is_file(),
                    "final_captured": (output_dir / f"{label}-final.txt").is_file(),
                    "agent_measurement_captured": (
                        output_dir / f"{label}-agent-measurement.json"
                    ).is_file(),
                }
                for label in ("arm-a", "arm-b")
            },
            "primary_policy_check_timing": {"status": "unscored", "elapsed_ms": None},
            "arms": arms,
        }
        if advice_policy == "nonbinding":
            receipt.update({
                "advice_policy": advice_policy,
                "decision_scope": "local_fixture_triage_no_remote_choice",
                "task_correctness_status": "passed" if task_correctness else "failed",
                "protocol_delivery_status": delivery_status,
                "adoption_status": adoption_status,
                "effect_scope": effect_scope,
            })
        common._private_write(
            output_dir / "receipt.json",
            (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="authorize execution of local Codex CLI arms")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--advice-policy", choices=ADVICE_POLICIES,
                        default="legacy-required-step",
                        help="nonbinding is an opt-in profile; legacy remains the default")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="new private directory for blind receipts and artifacts")
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("pass --live to run model-backed CLI arms")
    codex = shutil.which(args.codex)
    if not codex:
        print(json.dumps({"status": "failed", "failure": "codex_unavailable"}))
        return 2
    try:
        receipt = run_pair(
            codex=codex, model=args.model,
            reasoning_effort=args.reasoning_effort,
            timeout=args.timeout, seed=args.seed, output_dir=args.output_dir,
            advice_policy=args.advice_policy,
        )
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({"status": "failed", "failure": "runner_setup_failed"}))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
