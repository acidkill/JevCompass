#!/usr/bin/env python3
"""Run a bounded, keyless local-timeout triage guidance pair.

Both arms receive identical fixture files, contract, focused/full commands, and
supervisor-reviewed local evidence. Treatment adds one local next-check from
production triage; the caller supplies the verified observation, and it is not
a diagnosis or correctness result. The runner stores only redacted receipts.
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
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import pilot_cli_core as core
import pilot_test_order_pair as common
from pilot_receipts import parse_codex_json_events
from jevcompass.triage import (
    FailureKind,
    HypothesisId,
    NO_REMOTE_CHOICE,
    TimeoutObservation,
    triage_failure,
)


FIXTURE = ROOT / "tests" / "fixtures" / "condition_timeout_triage"
SOURCE_FILE = "inbox.py"
TEST_FILE = "tests/test_inbox.py"
CONTRACT_FILE = "CONTRACT.md"
README_FILE = "README.md"
FOCUSED_COMMAND = "python -m unittest discover -s tests -p 'test_inbox.py' -v"
REQUIRED_COMMAND = "python -m unittest discover -s tests -v"
EXPECTED_CODEX_VERSION = "0.157.0"
DEFAULT_TIMEOUT = min(180, core.MAX_TIMEOUT)
MAX_TIMEOUT = core.MAX_TIMEOUT
EVENT_LIMIT_BYTES = core.MAX_EVENT_BYTES
EXPECTED_TEST_COUNT = 5
EXPECTED_BASELINE_FOCUSED_COUNT = 5
REVIEWED_FIXTURE_HASHES = {
    "inbox.py": "92eebfd7ae4a6d32c2594764bc0c67770562fc2a970af274897439e04f945d08",
    "tests/test_inbox.py": "db8b8c6c45d2c1cd34f3d9996abd248ed4900eb78efdecb3c4399263ecce13f4",
    "CONTRACT.md": "03b8fbd54422da470ca44ad70461ca6982cdd2f7158f3326f7fd68aee785104e",
    "README.md": "7edd22c3ee95792d21145883256e09465b6b5eedcc41c612b67388c7206aac71",
}
USEFUL_FAILURE_MARKERS = (
    "ERROR: test_preloaded_item_is_available_without_waiting",
    "TimeoutError: inbox-not-ready",
)
PRETASK_ACK = (
    "PRETASK_ACK: I will verify the suggested wait-condition check against "
    "the shared fixture evidence before editing."
)
SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,64}")
TEST_COUNT_RE = re.compile(r"\bRan\s+(\d+)\s+tests?\s+in\s+[0-9.]+s\b")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fixture_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError("synthetic timeout fixture is unavailable")
    paths = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if ".git" in relative.parts or "__pycache__" in relative.parts:
            continue
        if path.is_symlink():
            raise ValueError("synthetic timeout fixture contains a symbolic link")
        if path.is_file() and path.suffix != ".pyc":
            paths.append(path)
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def _fixture_digest(root: Path, *, exclude_source: bool = False) -> str:
    digest = hashlib.sha256()
    for path in _fixture_files(root):
        relative = path.relative_to(root).as_posix()
        if exclude_source and relative == SOURCE_FILE:
            continue
        name = relative.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _source_digest(root: Path) -> str | None:
    path = root / SOURCE_FILE
    if path.is_symlink() or not path.is_file():
        return None
    return _sha256(path.read_bytes())


def _review_fixture(root: Path) -> dict[str, Any]:
    """Accept only the byte-exact reviewed fixture used to supply the timeout fact."""
    files = _fixture_files(root)
    observed_paths = {path.relative_to(root).as_posix() for path in files}
    if observed_paths != set(REVIEWED_FIXTURE_HASHES):
        raise ValueError("reviewed timeout fixture file set changed")
    observed_hashes = {
        path.relative_to(root).as_posix(): _sha256(path.read_bytes())
        for path in files
    }
    if observed_hashes != REVIEWED_FIXTURE_HASHES:
        raise ValueError("reviewed timeout fixture bytes changed")
    source_path = root / SOURCE_FILE
    test_path = root / TEST_FILE
    contract_path = root / CONTRACT_FILE
    for path in (source_path, test_path, contract_path, root / README_FILE):
        if path.is_symlink() or not path.is_file():
            raise ValueError("reviewed fixture evidence is unavailable")

    source = source_path.read_text(encoding="utf-8")
    tests = test_path.read_text(encoding="utf-8")
    contract = contract_path.read_text(encoding="utf-8")
    publish_start = source.find("    def publish(")
    take_start = source.find("    def take(", publish_start + 1)
    if publish_start < 0 or take_start < 0:
        raise ValueError("reviewed timeout evidence anchor changed")
    publish_body = source[publish_start:take_start]
    take_body = source[take_start:]
    test_start = tests.find("    def test_preloaded_item_is_available_without_waiting(")
    test_end = tests.find("\n    def test_", test_start + 1)
    if test_start < 0 or test_end < 0:
        raise ValueError("reviewed focused test anchor changed")
    preloaded_test = tests[test_start:test_end]

    verified = (
        source.count("self._ready = False") == 1
        and source.count("lambda: self._ready") == 1
        and source.count("_ready") == 2
        and "_items.append(value)" in publish_body
        and "self._condition.notify_all()" in publish_body
        and "_ready" not in publish_body
        and "wait_for(lambda: self._ready, timeout)" in take_body
        and "inbox.publish(token)" in preloaded_test
        and "inbox.take(timeout=0)" in preloaded_test
        and "self.assertIs(inbox.take(timeout=0), token)" in preloaded_test
        and "Already queued values are available" in contract
    )
    if not verified:
        raise ValueError("reviewed timeout evidence did not match the frozen fixture")

    return {
        "status": "verified",
        "observation": TimeoutObservation.WAIT_CONDITION_UNSATISFIABLE,
        "source_sha256": _source_digest(root),
        "focused_test_sha256": _sha256(test_path.read_bytes()),
        "immutable_fixture_sha256": _fixture_digest(root, exclude_source=True),
        "review_scope": "readiness initialization, publisher state change, wait predicate, preloaded zero-timeout test",
        "fact_authority": "caller_reviewed_fixture_evidence_not_triage_api_diagnosis",
    }


class _NoRemoteClient:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        raise AssertionError("remote triage is forbidden in this local pilot")


def _prepare_local_step(root: Path, observed_exit_status: int) -> tuple[dict[str, Any], str]:
    evidence = _review_fixture(root)
    client = _NoRemoteClient()
    result = triage_failure(
        (FailureKind.TIMEOUT,),
        (HypothesisId.TIMEOUT_CONTENTION, HypothesisId.TIMEOUT_NONTERMINATING),
        observed_exit_status,
        client,
        timeout_observations=(evidence["observation"],),
    )
    if (
        observed_exit_status != 1
        or result.observed_exit_status != observed_exit_status
        or not result.test_failed
        or result.status != NO_REMOTE_CHOICE
        or tuple(step.id for step in result.steps) != (HypothesisId.TIMEOUT_NONTERMINATING,)
        or client.calls != 0
    ):
        raise ValueError("local triage did not produce the expected bounded next-check")
    step = result.steps[0]
    evidence["triage_status"] = result.status
    evidence["next_check_id"] = step.id.value
    evidence["next_check_text"] = step.instruction
    evidence["triage_api_diagnosis"] = False
    evidence["remote_invocation_count"] = client.calls
    return evidence, step.instruction


def _lock_fixture_files(root: Path) -> None:
    for path in _fixture_files(root):
        if path.relative_to(root).as_posix() == SOURCE_FILE:
            os.chmod(path, 0o600)
        else:
            os.chmod(path, 0o444)


def _expected_argv(command: str) -> tuple[str, ...]:
    return tuple(shlex.split(command))


def _command_kind(item: dict[str, Any]) -> str | None:
    argv = common._command_argv(item)
    if not argv:
        return None
    normalized = tuple(argv)
    if normalized == _expected_argv(FOCUSED_COMMAND):
        return "focused"
    if normalized == _expected_argv(REQUIRED_COMMAND):
        return "full"
    return None


def _item(event: dict[str, Any]) -> dict[str, Any] | None:
    value = event.get("item")
    return value if isinstance(value, dict) else None


def _event_ms(event_times: list[float], index: int, started: float) -> float | None:
    if index >= len(event_times):
        return None
    elapsed = (event_times[index] - started) * 1000
    if elapsed < 0:
        return None
    return round(elapsed, 2)


def _matches_useful_failure(output: Any) -> bool:
    return isinstance(output, str) and all(marker in output for marker in USEFUL_FAILURE_MARKERS)


def _event_receipts(
    lines: Iterable[str], event_times: list[float], started: float, *, treatment: bool,
) -> dict[str, Any]:
    pending: dict[str, str] = {}
    focused: list[dict[str, Any]] = []
    full: list[dict[str, Any]] = []
    first_tool_index: int | None = None
    first_agent_message: tuple[int, str] | None = None
    malformed_sequence = False

    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        item = _item(event)
        if item is None:
            continue
        event_type = event.get("type")
        item_type = item.get("type")
        if event_type == "item.completed" and item_type == "agent_message":
            if first_agent_message is None:
                text = item.get("text")
                first_agent_message = (index, text if isinstance(text, str) else "")
        if event_type == "item.started":
            if item_type != "agent_message" and first_tool_index is None:
                first_tool_index = index
            kind = _command_kind(item)
            identifier = item.get("id")
            if kind is not None and isinstance(identifier, str) and identifier:
                if identifier in pending:
                    malformed_sequence = True
                    pending.pop(identifier, None)
                else:
                    pending[identifier] = kind
        elif event_type == "item.completed":
            identifier = item.get("id")
            if not isinstance(identifier, str) or identifier not in pending:
                continue
            kind = pending.pop(identifier)
            exit_code = item.get("exit_code")
            if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                continue
            output = item.get("aggregated_output")
            if not isinstance(output, str):
                output = item.get("output")
            observed = {
                "exit_code": exit_code,
                "completed_ms": _event_ms(event_times, index, started),
                "useful_failure_markers": (
                    _matches_useful_failure(output) if kind == "focused" and exit_code == 1 else None
                ),
            }
            (focused if kind == "focused" else full).append(observed)

    first_focus = focused[0] if focused else None
    useful_failure = bool(
        first_focus is not None
        and first_focus["exit_code"] == 1
        and first_focus["useful_failure_markers"] is True
    )
    focused_pass_after_failure = any(
        item["exit_code"] == 0 for item in focused[1:]
    ) if first_focus is not None and first_focus["exit_code"] == 1 else False
    first_successful_check_ms = next(
        (item["completed_ms"] for item in focused[1:]
         if item["exit_code"] == 0 and item["completed_ms"] is not None),
        None,
    )
    full_after_focused_pass = bool(
        focused_pass_after_failure
        and first_successful_check_ms is not None
        and any(
            item["exit_code"] == 0
            and item["completed_ms"] is not None
            and item["completed_ms"] >= first_successful_check_ms
            for item in full
        )
    )
    initial_ack = None
    if treatment:
        initial_ack = bool(
            first_agent_message is not None
            and first_tool_index is not None
            and first_agent_message[0] < first_tool_index
            and PRETASK_ACK in first_agent_message[1]
        )
    codex_usage = parse_codex_json_events(
        lines,
        started_at=started,
        ended_at=event_times[-1] if event_times else started,
        event_times=event_times,
    )
    return {
        "initial_focused_exit": first_focus["exit_code"] if first_focus else None,
        "initial_useful_error_status": "observed" if useful_failure else "unscored",
        "first_useful_error_ms": (
            first_focus["completed_ms"]
            if first_focus is not None and useful_failure else None
        ),
        "focused_exit_codes": [item["exit_code"] for item in focused],
        "focused_success_after_failure": focused_pass_after_failure,
        "first_successful_focused_check_ms": first_successful_check_ms,
        "full_suite_exit_codes": [item["exit_code"] for item in full],
        "full_suite_exit": full[-1]["exit_code"] if full else None,
        "full_suite_invocation_observed": bool(full),
        "full_suite_pass_after_focused_pass": full_after_focused_pass,
        "initial_ack_before_tools": initial_ack,
        "event_sequence_valid": not malformed_sequence,
        "event_count": len(list(lines)) if isinstance(lines, list) else None,
        "token_usage_status": codex_usage["usage_status"],
        "token_usage": codex_usage["token_usage"],
        "billing_estimate": None,
    }


def _empty_arm(failure: str) -> dict[str, Any]:
    return {
        "cli_status": "failed",
        "failure": failure,
        "cli_exit_code": None,
        "agent_completion_ms": None,
        "initial_focused_exit": None,
        "initial_useful_error_status": "unscored",
        "first_useful_error_ms": None,
        "focused_exit_codes": [],
        "focused_success_after_failure": False,
        "first_successful_focused_check_ms": None,
        "full_suite_exit_codes": [],
        "full_suite_exit": None,
        "full_suite_invocation_observed": False,
        "full_suite_pass_after_focused_pass": False,
        "initial_ack_before_tools": None,
        "event_sequence_valid": False,
        "event_count": None,
        "token_usage_status": "unscored",
        "token_usage": None,
        "billing_estimate": None,
    }


def _run_arm(
    *,
    codex: str,
    model: str,
    reasoning_effort: str,
    prompt: str,
    fixture: Path,
    home: Path,
    timeout: int,
    treatment: bool,
) -> dict[str, Any]:
    (home / ".codex").mkdir(mode=0o700, parents=True, exist_ok=True)
    env = core._isolated_environment(
        home=home,
        isolated_python=home / "python",
        allow_openrouter_key=False,
    )
    env.pop("OPENROUTER_API_KEY", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            common._cli_command(codex, model, reasoning_effort, prompt, allow_network=False),
            cwd=fixture,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        lines, times, failure = core._collect_events(
            process, started=started, timeout=timeout, preserve_on_failure=True,
        )
    except OSError:
        return _empty_arm("codex_unavailable")
    completion_ms = round((time.monotonic() - started) * 1000, 2)
    observed = _event_receipts(lines, times, started, treatment=treatment)
    cli_ok = failure is None and process.returncode == 0
    return {
        "cli_status": "completed" if cli_ok else "failed",
        "failure": failure if failure is not None else (None if cli_ok else "cli_exit_nonzero"),
        "cli_exit_code": process.returncode if failure is None else None,
        "agent_completion_ms": completion_ms,
        **observed,
    }


def _test_count(output: str) -> int | None:
    matches = TEST_COUNT_RE.findall(output)
    if len(matches) != 1:
        return None
    try:
        return int(matches[0])
    except ValueError:
        return None


def _run_validation_command(
    *,
    command: str,
    fixture: Path,
    home: Path,
    timeout: int,
) -> dict[str, Any]:
    argv = [sys.executable, "-B", *shlex.split(command)[1:]]
    env = core._isolated_environment(
        home=home,
        isolated_python=home / "python",
        allow_openrouter_key=False,
    )
    env.pop("OPENROUTER_API_KEY", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv,
            cwd=fixture,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        lines, _times, failure = core._collect_events(
            process, started=started, timeout=timeout, preserve_on_failure=True,
        )
    except OSError:
        return {"status": "unscored", "exit_code": None, "test_count": None, "elapsed_ms": None}
    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    output = "\n".join(lines)
    count = _test_count(output)
    useful_failure_markers = (
        _matches_useful_failure(output)
        if command == FOCUSED_COMMAND and process.returncode == 1 and failure is None
        else None
    )
    passed = (
        failure is None
        and process.returncode == 0
        and count == EXPECTED_TEST_COUNT
        and re.search(r"(?m)^OK$", output) is not None
    )
    return {
        "status": "passed" if passed else "failed",
        "exit_code": process.returncode if failure is None else None,
        "test_count": count,
        "elapsed_ms": elapsed_ms,
        "useful_failure_markers": useful_failure_markers,
    }


def _independent_validation(
    fixture: Path,
    home: Path,
    timeout: int,
) -> dict[str, Any]:
    focused = _run_validation_command(
        command=FOCUSED_COMMAND, fixture=fixture, home=home / "focused",
        timeout=timeout,
    )
    full = _run_validation_command(
        command=REQUIRED_COMMAND, fixture=fixture, home=home / "full",
        timeout=timeout,
    )
    return {
        "focused": focused,
        "full": full,
        "status": "passed" if focused["status"] == "passed" and full["status"] == "passed" else "failed",
    }


def _skipped_independent_validation(reason: str) -> dict[str, Any]:
    skipped = {
        "status": "skipped",
        "reason": reason,
        "exit_code": None,
        "test_count": None,
        "elapsed_ms": None,
        "useful_failure_markers": None,
    }
    return {"focused": dict(skipped), "full": dict(skipped), "status": "failed", "reason": reason}


def _verify_codex_version(codex: str) -> bool:
    try:
        result = subprocess.run(
            [codex, "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    version_text = (result.stdout or "") + "\n" + (result.stderr or "")
    return result.returncode == 0 and EXPECTED_CODEX_VERSION in version_text


def _private_receipt(output_dir: Path, receipt: dict[str, Any]) -> None:
    common._private_write(
        output_dir / "receipt.json",
        (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def run_pair(
    *,
    codex: str,
    model: str,
    reasoning_effort: str,
    timeout: int = DEFAULT_TIMEOUT,
    seed: int | None = None,
    output_dir: Path,
    fixture_source: Path = FIXTURE,
) -> dict[str, Any]:
    """Run an isolated matched pair; never contacts Jev/OpenRouter or stores raw events."""
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if not SAFE_IDENTIFIER.fullmatch(model) or not SAFE_IDENTIFIER.fullmatch(reasoning_effort):
        raise ValueError("model and reasoning effort must be simple identifiers")
    if not fixture_source.is_dir():
        raise FileNotFoundError("synthetic timeout fixture is unavailable")
    if not _verify_codex_version(codex):
        raise ValueError(f"Codex CLI {EXPECTED_CODEX_VERSION} is required")
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError("output path already exists; choose a fresh run directory")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValueError("output directory must be a real directory")
    os.chmod(output_dir, 0o700)

    run_started = time.monotonic()
    run_id = uuid.uuid4().hex
    order = ["baseline", "treatment"]
    random.Random(seed).shuffle(order)
    labels = {arm: f"arm-{'a' if order[0] == arm else 'b'}" for arm in order}
    source_auth = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"

    with tempfile.TemporaryDirectory(prefix="jev-condition-timeout-pair-") as temporary:
        private_root = Path(temporary)
        os.chmod(private_root, 0o700)
        evidence_dir = private_root / "preflight-fixture"
        common._copy_identical_fixture(fixture_source, evidence_dir)
        prep_started = time.monotonic()
        evidence: dict[str, Any]
        next_check: str
        try:
            preliminary = _review_fixture(evidence_dir)
            validation_home = private_root / "preflight-home"
            baseline_validation = _run_validation_command(
                command=FOCUSED_COMMAND,
                fixture=evidence_dir,
                home=validation_home,
                timeout=min(timeout, 15),
            )
            initial_exit = baseline_validation["exit_code"]
            if (
                initial_exit != 1
                or baseline_validation["test_count"] != EXPECTED_BASELINE_FOCUSED_COUNT
                or baseline_validation["status"] != "failed"
                or baseline_validation["useful_failure_markers"] is not True
            ):
                raise ValueError("focused local preflight did not verify the frozen useful timeout failure")
            evidence, next_check = _prepare_local_step(evidence_dir, initial_exit)
            evidence["preflight_focused_exit"] = initial_exit
            evidence["preflight_focused_test_count"] = baseline_validation["test_count"]
            evidence["preflight_focused_status"] = baseline_validation["status"]
            evidence["preflight_elapsed_ms"] = baseline_validation["elapsed_ms"]
            evidence["preflight_test_sha256"] = preliminary["focused_test_sha256"]
            evidence["immutable_fixture_sha256"] = preliminary["immutable_fixture_sha256"]
        except (OSError, ValueError, TypeError):
            receipt = {
                "schema_version": 1,
                "run_id": run_id,
                "status": "incomplete",
                "failure": "local_preflight_unverified",
                "run_status_scope": "protocol_and_frozen_correctness_only_not_advice_efficacy",
                "advice_utility_status": "unscored",
                "arms": {},
            }
            _private_receipt(output_dir, receipt)
            return receipt
        preparation_ms = round((time.monotonic() - prep_started) * 1000, 2)

        shared_evidence = (
            "\n\nShared local preflight evidence (identical for both arms): "
            "the focused frozen test checks a preloaded item at timeout zero; "
            "the publisher queues and notifies, while the consumer waits on a "
            "separate readiness predicate. This is reviewed fixture evidence, "
            "not a conclusion from the triage API. Verify it against the local "
            "contract, source, and tests before editing."
        )
        base_prompt = (fixture_source / README_FILE).read_text(encoding="utf-8") + shared_evidence
        treatment_prompt = (
            base_prompt
            + "\n\nA local triage next-check, prepared from the shared verified "
            "evidence (suggestion only, not a confirmed cause or success): "
            + next_check
            + "\nVerify the suggestion against the same local files before "
            "editing. Your first response must begin with this exact acknowledgement "
            "before any tool call: "
            + PRETASK_ACK
        )
        prompts_equal_except_guidance = (
            treatment_prompt.removeprefix(base_prompt).startswith(
                "\n\nA local triage next-check"
            )
            and base_prompt in treatment_prompt
        )
        auth_snapshot = private_root / "auth-snapshot.json"
        try:
            auth_ok = core._copy_auth(source_auth, auth_snapshot)
        except OSError:
            auth_ok = False
        if not auth_ok:
            receipt = {
                "schema_version": 1,
                "run_id": run_id,
                "status": "failed",
                "failure": "auth_unavailable",
                "preparation_ms": preparation_ms,
                "arms": {},
            }
            _private_receipt(output_dir, receipt)
            return receipt

        fixtures: dict[str, Path] = {}
        homes: dict[str, Path] = {}
        initial_full_hashes: dict[str, str] = {}
        initial_immutable_hashes: dict[str, str] = {}
        initial_source_hashes: dict[str, str | None] = {}
        for arm in ("baseline", "treatment"):
            fixtures[arm] = private_root / f"{arm}-fixture"
            homes[arm] = private_root / f"{arm}-home"
            common._copy_identical_fixture(fixture_source, fixtures[arm])
            initial_full_hashes[arm] = _fixture_digest(fixtures[arm])
            initial_immutable_hashes[arm] = _fixture_digest(fixtures[arm], exclude_source=True)
            initial_source_hashes[arm] = _source_digest(fixtures[arm])
            if not core._copy_auth(auth_snapshot, homes[arm] / ".codex" / "auth.json"):
                receipt = {
                    "schema_version": 1,
                    "run_id": run_id,
                    "status": "failed",
                    "failure": "auth_setup_failed",
                    "preparation_ms": preparation_ms,
                    "arms": {},
                }
                _private_receipt(output_dir, receipt)
                return receipt
            _lock_fixture_files(fixtures[arm])
        if initial_full_hashes["baseline"] != initial_full_hashes["treatment"]:
            raise RuntimeError("fixture parity verification failed")

        arms: dict[str, dict[str, Any]] = {}
        for true_arm in order:
            label = labels[true_arm]
            prompt = base_prompt if true_arm == "baseline" else treatment_prompt
            arm = _run_arm(
                codex=codex,
                model=model,
                reasoning_effort=reasoning_effort,
                prompt=prompt,
                fixture=fixtures[true_arm],
                home=homes[true_arm],
                timeout=timeout,
                treatment=true_arm == "treatment",
            )
            arm["fixture_sha256_before"] = initial_full_hashes[true_arm]
            arm["source_sha256_before"] = initial_source_hashes[true_arm]
            arms[label] = arm

        immutable_preserved: dict[str, bool] = {}
        source_hashes_after: dict[str, str | None] = {}
        independent: dict[str, dict[str, Any]] = {}
        for true_arm in ("baseline", "treatment"):
            label = labels[true_arm]
            immutable_preserved[label] = (
                _fixture_digest(fixtures[true_arm], exclude_source=True)
                == initial_immutable_hashes[true_arm]
            )
            source_hashes_after[label] = _source_digest(fixtures[true_arm])
            if not immutable_preserved[label]:
                validation = _skipped_independent_validation("immutable_fixture_changed")
            elif source_hashes_after[label] is None:
                validation = _skipped_independent_validation("source_not_regular_file")
            else:
                validation = _independent_validation(
                    fixtures[true_arm], homes[true_arm] / "independent",
                    timeout=min(timeout, 30),
                )
            independent[label] = validation

        mapping = {
            "run_id": run_id,
            "arm-a": "baseline" if labels["baseline"] == "arm-a" else "treatment",
            "arm-b": "baseline" if labels["baseline"] == "arm-b" else "treatment",
        }
        common._private_write(
            output_dir / "arm-map.json",
            (json.dumps(mapping, sort_keys=True) + "\n").encode("utf-8"),
        )
        arm_gates = {
            label: (
                arm.get("cli_status") == "completed"
                and arm.get("initial_focused_exit") == 1
                and arm.get("initial_useful_error_status") == "observed"
                and arm.get("focused_success_after_failure") is True
                and arm.get("full_suite_exit") == 0
                and arm.get("full_suite_invocation_observed") is True
                and arm.get("full_suite_pass_after_focused_pass") is True
                and arm.get("event_sequence_valid") is True
                and immutable_preserved.get(label) is True
                and arm.get("source_sha256_before") is not None
                and source_hashes_after.get(label) != arm.get("source_sha256_before")
                and independent.get(label, {}).get("status") == "passed"
            )
            for label, arm in arms.items()
        }
        treatment_label = labels["treatment"]
        treatment_ack = arms.get(treatment_label, {}).get("initial_ack_before_tools") is True
        protocol_complete = (
            len(arms) == 2
            and all(arm_gates.values())
            and treatment_ack
            and prompts_equal_except_guidance
        )
        for label, arm in arms.items():
            arm["immutable_files_preserved"] = immutable_preserved.get(label, False)
            arm["source_sha256_after"] = source_hashes_after.get(label)
            arm["source_changed"] = (
                arm.get("source_sha256_before") is not None
                and source_hashes_after.get(label) != arm.get("source_sha256_before")
            )
            arm["independent_frozen_validation"] = independent.get(label)
            arm["total_including_preparation_ms"] = (
                round(preparation_ms + arm["agent_completion_ms"], 2)
                if isinstance(arm.get("agent_completion_ms"), (int, float))
                else None
            )

        receipt = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "completed" if protocol_complete else "incomplete",
            "run_status_scope": "protocol_and_frozen_correctness_only_not_advice_efficacy",
            "advice_utility_status": "unscored",
            "pair_order": [labels[arm] for arm in order],
            "timeout_seconds_per_arm": timeout,
            "event_limit_bytes": EVENT_LIMIT_BYTES,
            "randomization_seed": seed,
            "focused_command": FOCUSED_COMMAND,
            "required_command": REQUIRED_COMMAND,
            "codex_cli_version_required": EXPECTED_CODEX_VERSION,
            "openrouter_key_allowed": False,
            "remote_triage_invocations": evidence["remote_invocation_count"],
            "preparation_ms": preparation_ms,
            "pair_total_elapsed_ms": round((time.monotonic() - run_started) * 1000, 2),
            "prompt_hashes": {
                "shared_base_sha256": _sha256(base_prompt.encode("utf-8")),
                "treatment_sha256": _sha256(treatment_prompt.encode("utf-8")),
                "same_shared_evidence": base_prompt in treatment_prompt,
                "only_preset_next_check_added": prompts_equal_except_guidance,
            },
            "preflight": {
                key: value for key, value in evidence.items()
                if key != "observation" and key != "next_check_text"
            },
            "fixture_parity_sha256": initial_full_hashes["baseline"],
            "source_sha256_before": {
                labels[arm]: initial_source_hashes[arm] for arm in ("baseline", "treatment")
            },
            "immutable_fixture_sha256_before": {
                labels[arm]: initial_immutable_hashes[arm] for arm in ("baseline", "treatment")
            },
            "immutable_files_preserved": immutable_preserved,
            "arm_gates": arm_gates,
            "treatment_ack_before_tools": treatment_ack,
            "independent_frozen_validation": independent,
            "arms": arms,
        }
        _private_receipt(output_dir, receipt)
        return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pilot_condition_timeout_pair",
        description="Run a keyless local-triage guidance pair on the frozen condition-timeout fixture.",
    )
    parser.add_argument("--codex", default="codex", help="Codex CLI executable")
    parser.add_argument("--model", required=True, help="Codex CLI model identifier")
    parser.add_argument("--reasoning-effort", required=True, help="Codex CLI reasoning effort")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Maximum seconds per arm")
    parser.add_argument("--seed", type=int, help="Seed for blinded arm ordering")
    parser.add_argument("--output-dir", required=True, type=Path, help="New private output directory")
    args = parser.parse_args(argv)
    try:
        receipt = run_pair(
            codex=args.codex,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            timeout=args.timeout,
            seed=args.seed,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError, RuntimeError) as error:
        print(f"condition-timeout pilot failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
