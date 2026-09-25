#!/usr/bin/env python3
"""Run one bounded, randomized synthetic CLI pair for the test-order pilot.

The baseline and treatment receive byte-identical disposable fixtures, the
same explicit Codex model/settings, and separately copied auth profiles. Only
the treatment prompt asks JevCompass to rank the two declared focused tests.
Raw Codex events and all prompt/source/output text stay in memory.
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
from pilot_receipts import parse_choice_receipt, parse_codex_json_events


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "coding_test_order"
CHANGED_FILE = "parcelquote/quote.py"
MAX_ARTIFACT_BYTES = 64 * 1024
MAX_CHOICE_OUTPUT_BYTES = 64 * 1024
MAX_TIMEOUT = core.MAX_TIMEOUT
DEFAULT_TIMEOUT = min(180, MAX_TIMEOUT)
EVENT_LIMIT = core.MAX_EVENT_BYTES

UNIT_COMMAND = "python -m unittest discover -s tests -p 'test_unit*.py' -v"
CONTRACT_COMMAND = "python -m unittest discover -s tests -p 'test_contract*.py' -v"
REQUIRED_COMMAND = "python -m unittest discover -s tests -v"
CANDIDATES = (
    {"kind": "unit", "command": UNIT_COMMAND, "relevance": 0.8, "id": "unit"},
    {"kind": "contract", "command": CONTRACT_COMMAND, "relevance": 0.8, "id": "contract"},
)
REQUIRED = ({"command": REQUIRED_COMMAND, "id": "full"},)
CHOICE_IDS = ("unit", "contract")

BASE_PROMPT = (
    "Fix ParcelQuote's shipping quote calculation so every positive partial "
    "kilogram is charged as a whole started kilogram. Exact kilogram weights "
    "must keep their current price. Preserve input validation and the existing "
    "JSON command-line interface. Work only in this synthetic fixture. "
    f"After editing, run one focused check from the declared candidates, then "
    f"always run the required suite: {REQUIRED_COMMAND}. Do not add tests or "
    "change unrelated files. Report no source text in the final response."
)
TREATMENT_RANKING = (
    "\n\nAfter your code edit, ask JevCompass to rank exactly these two "
    "focused candidates and retain the required suite as a mandatory gate. "
    "Run the local command python -m jevcompass tests rank --input - --json with this "
    "JSON on stdin (the rank command only orders tests; it does not execute "
    "them): "
    + json.dumps(
        {"surface": "python", "candidates": list(CANDIDATES), "required": list(REQUIRED)},
        separators=(",", ":"),
    )
    + ". Follow the returned focused order by running one candidate, then "
    "always run the required full suite yourself. Do not skip or infer the "
    "required suite result from Codex's overall exit status."
)
SAFE_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
SAFE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")


def _item(event: dict[str, Any]) -> dict[str, Any] | None:
    item = event.get("item")
    if not isinstance(item, dict):
        item = event.get("payload")
    return item if isinstance(item, dict) else None


def _command_argv(item: dict[str, Any]) -> list[str] | None:
    raw = item.get("command")
    if isinstance(raw, list) and all(isinstance(part, str) for part in raw):
        argv = raw
    elif isinstance(raw, str):
        try:
            argv = shlex.split(raw)
        except ValueError:
            return None
    else:
        return None
    if (len(argv) == 3 and argv[0] in {"bash", "/bin/bash", "/usr/bin/bash"}
            and argv[1] == "-lc"):
        try:
            argv = shlex.split(argv[2])
        except ValueError:
            return None
    return argv


def _test_kind(item: dict[str, Any]) -> str | None:
    argv = _command_argv(item)
    if argv == shlex.split(UNIT_COMMAND):
        return "unit"
    if argv == shlex.split(CONTRACT_COMMAND):
        return "contract"
    if argv == shlex.split(REQUIRED_COMMAND):
        return "required"
    return None


def _rank_invocation(item: dict[str, Any]) -> bool:
    argv = _command_argv(item)
    if not argv:
        return False
    return (
        "jevcompass" in argv
        and any(argv[index:index + 3] == ["tests", "rank", "--input"]
                for index in range(len(argv)))
        and "-" in argv
        and "--json" in argv
    )


def _strict_json(text: str) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=pairs)


def _validated_choice(payload: str | None) -> dict[str, Any]:
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_CHOICE_OUTPUT_BYTES:
        return {"status": "unscored", "candidate_ids": []}
    try:
        value = _strict_json(payload)
    except (ValueError, TypeError, json.JSONDecodeError):
        return {"status": "unscored", "candidate_ids": []}
    if not isinstance(value, dict) or value.get("executed") is not False:
        return {"status": "unscored", "candidate_ids": []}
    ordered = value.get("ordered")
    required = value.get("required")
    expected_commands = {"unit": UNIT_COMMAND, "contract": CONTRACT_COMMAND}
    if (not isinstance(ordered, list) or len(ordered) != len(expected_commands)
            or not isinstance(required, list)
            or len(required) != 1 or not isinstance(required[0], dict)
            or required[0] != {"id": "full", "command": REQUIRED_COMMAND}):
        return {"status": "unscored", "candidate_ids": []}
    seen: set[str] = set()
    for candidate in ordered:
        if (not isinstance(candidate, dict)
                or candidate.get("id") not in expected_commands
                or candidate.get("command") != expected_commands[candidate["id"]]
                or candidate.get("kind") != candidate["id"]
                or candidate["id"] in seen):
            return {"status": "unscored", "candidate_ids": []}
        seen.add(candidate["id"])
    return parse_choice_receipt(value, choice_type="test_order", candidate_ids=CHOICE_IDS)


def _event_receipts(
    lines: Iterable[str], event_times: list[float], started: float,
) -> dict[str, Any]:
    pending_tests: dict[str, str] = {}
    completed_tests: list[dict[str, Any]] = []
    first_observed_focused_failure_ms: float | None = None
    first_useful_error_ms: float | None = None
    pending_ranks: dict[str, float] = {}
    rank_payloads: list[tuple[float, str]] = []
    rank_exit_code: int | None = None
    rank_output_status = "not_invoked"
    command_counts = {"unit": 0, "contract": 0, "required": 0}
    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        item = _item(event)
        if item is None:
            continue
        raw_id = item.get("id")
        event_id = raw_id if isinstance(raw_id, str) and 0 < len(raw_id) <= 128 else None
        kind = _test_kind(item)
        if event_type == "item.started" and event_id:
            if kind:
                if kind in pending_tests.values():
                    pending_tests = {key: value for key, value in pending_tests.items()
                                     if value != kind}
                pending_tests[event_id] = kind
                command_counts[kind] += 1
            if _rank_invocation(item):
                start_time = event_times[index] if index < len(event_times) else time.monotonic()
                pending_ranks[event_id] = start_time
        elif event_type == "item.completed" and event_id:
            started_kind = pending_tests.pop(event_id, None)
            if started_kind and (kind is None or kind == started_kind):
                exit_code = item.get("exit_code")
                if isinstance(exit_code, int) and not isinstance(exit_code, bool):
                    completed_tests.append({"kind": started_kind, "exit_code": exit_code})
                    if started_kind in CHOICE_IDS and exit_code != 0:
                        observed = event_times[index] if index < len(event_times) else time.monotonic()
                        elapsed_ms = round(max(0.0, (observed - started) * 1000), 2)
                        if first_observed_focused_failure_ms is None:
                            first_observed_focused_failure_ms = elapsed_ms
                        output = item.get("aggregated_output")
                        if not isinstance(output, str):
                            output = item.get("output")
                        expected_case = ("test_partial_kilogram_rounds_up" if started_kind == "unit"
                                         else "test_partial_kilogram_json_contract")
                        if (first_useful_error_ms is None and isinstance(output, str)
                                and re.search(r"(?:FAIL|ERROR):\s*" + expected_case + r"\b", output)):
                            first_useful_error_ms = elapsed_ms
            if event_id in pending_ranks:
                rank_started = pending_ranks.pop(event_id)
                raw_exit = item.get("exit_code")
                rank_exit_code = raw_exit if isinstance(raw_exit, int) and not isinstance(raw_exit, bool) else None
                output = item.get("aggregated_output")
                if not isinstance(output, str):
                    output = item.get("output")
                observed = event_times[index] if index < len(event_times) else time.monotonic()
                elapsed = max(0.0, (observed - rank_started) * 1000)
                if isinstance(output, str):
                    rank_payloads.append((elapsed, output))
                    rank_output_status = "captured"
                else:
                    rank_output_status = "missing_output"
    choice = (
        _validated_choice(rank_payloads[-1][1])
        if rank_payloads else {"status": "unscored", "candidate_ids": []}
    )
    if rank_output_status == "captured":
        rank_output_status = "valid_choice" if choice["status"] != "unscored" else "invalid_choice"
    focused: list[dict[str, Any]] = []
    for candidate in CHOICE_IDS:
        matched = next((entry["exit_code"] for entry in reversed(completed_tests)
                        if entry["kind"] == candidate), None)
        if matched is not None:
            focused.append({"candidate_id": candidate, "exit_code": matched})
    required_exit = next((entry["exit_code"] for entry in reversed(completed_tests)
                          if entry["kind"] == "required"), None)
    return {
        "choice": choice,
        "choice_latency_ms": round(rank_payloads[-1][0], 2) if rank_payloads else None,
        "rank_exit_code": rank_exit_code,
        "rank_output_status": rank_output_status,
        "focused_test_exits": focused,
        "first_observed_focused_failure_ms": first_observed_focused_failure_ms,
        "first_useful_error_ms": first_useful_error_ms,
        "focused_invocation_count": command_counts["unit"] + command_counts["contract"],
        "required_suite_exit": required_exit,
        "required_suite_invocation_observed": command_counts["required"] > 0,
    }


def _private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def _safe_final_artifact(fixture: Path) -> bytes | None:
    path = fixture / CHANGED_FILE
    try:
        if path.is_symlink() or not path.is_file():
            return None
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
            return None
        data = path.read_bytes()
        return data if len(data) <= MAX_ARTIFACT_BYTES else None
    except OSError:
        return None


def _copy_identical_fixture(source: Path, destination: Path) -> str:
    if not source.is_dir() or any("expected" in path.name.lower() for path in source.rglob("*")):
        raise ValueError("synthetic coding fixture is unavailable or contains evaluator material")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("synthetic coding fixture contains a symbolic link")
    shutil.copytree(source, destination, symlinks=True)
    return core.fixture_digest(destination)


def _cli_command(codex: str, model: str, reasoning_effort: str, prompt: str) -> list[str]:
    if (not SAFE_MODEL.fullmatch(model) or not SAFE_ID.fullmatch(reasoning_effort)):
        raise ValueError("model and reasoning effort must be simple identifiers")
    return [
        codex, "-a", "never", "exec", "--json", "--ephemeral",
        "--sandbox", "workspace-write", "--skip-git-repo-check",
        "--dangerously-bypass-hook-trust", "--model", model,
        "--config", f"model_reasoning_effort={reasoning_effort}", prompt,
    ]


def _run_arm(
    *, codex: str, model: str, reasoning_effort: str, prompt: str,
    fixture: Path, home: Path, timeout: int, allow_openrouter_key: bool,
) -> dict[str, Any]:
    codex_home = home / ".codex"
    codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = core._isolated_environment(
        home=home, isolated_python=home / "python",
        allow_openrouter_key=allow_openrouter_key,
    )
    if not allow_openrouter_key:
        env.pop("OPENROUTER_API_KEY", None)
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            _cli_command(codex, model, reasoning_effort, prompt),
            cwd=fixture, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        lines, times, failure = core._collect_events(
            process, started=started, timeout=timeout,
        )
    except OSError:
        return {"cli_status": "failed", "failure": "codex_unavailable"}
    wall_ms = round((time.monotonic() - started) * 1000, 2)
    if failure:
        return {
            "cli_status": "failed", "failure": failure,
            "completion_ms": wall_ms if wall_ms <= MAX_TIMEOUT * 1000 else None,
            "event_count": 0,
            "token_usage_status": "unscored", "token_usage": None,
            "billing_estimate": None,
            "choice": {"status": "unscored", "candidate_ids": []},
            "choice_latency_ms": None, "rank_exit_code": None,
            "rank_output_status": "not_invoked", "focused_test_exits": [],
            "first_observed_focused_failure_ms": None, "first_useful_error_ms": None,
            "focused_invocation_count": 0, "required_suite_exit": None,
            "required_suite_invocation_observed": False,
        }
    observed = _event_receipts(lines, times, started)
    counter_receipt = parse_codex_json_events(
        lines, started_at=started, ended_at=time.monotonic(), event_times=times,
    )
    return {
        "cli_status": "completed" if process.returncode == 0 else "failed",
        "failure": None,
        "cli_exit_code": process.returncode,
        "completion_ms": wall_ms if wall_ms <= MAX_TIMEOUT * 1000 else None,
        "event_count": len(lines),
        "token_usage_status": counter_receipt["usage_status"],
        "token_usage": counter_receipt["token_usage"],
        "billing_estimate": None,
        **observed,
    }


def run_pair(
    *, codex: str, model: str, reasoning_effort: str, timeout: int = DEFAULT_TIMEOUT,
    seed: int | None = None, output_dir: Path, fixture_source: Path = FIXTURE,
    allow_openrouter_key: bool = False,
) -> dict[str, Any]:
    """Run both isolated arms and write private, redacted receipts/artifact."""
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if not fixture_source.is_dir():
        raise FileNotFoundError("synthetic coding fixture is unavailable")
    if allow_openrouter_key and not os.environ.get("OPENROUTER_API_KEY"):
        raise ValueError("the opted-in API key is unavailable")
    try:
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ValueError("output path already exists; choose a fresh run directory") from error
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValueError("output directory must be a real directory")
    rng = random.Random(seed)
    true_order = ["baseline", "treatment"]
    rng.shuffle(true_order)
    arm_labels = {"baseline": "arm-a", "treatment": "arm-b"}
    if true_order[0] == "treatment":
        arm_labels = {"treatment": "arm-a", "baseline": "arm-b"}
    run_id = uuid.uuid4().hex
    source_auth = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"

    with tempfile.TemporaryDirectory(prefix="jev-test-order-pair-") as temporary:
        private_root = Path(temporary)
        os.chmod(private_root, 0o700)
        auth_snapshot = private_root / "auth-snapshot.json"
        try:
            auth_ok = core._copy_auth(source_auth, auth_snapshot)
        except OSError:
            auth_ok = False
        if not auth_ok:
            return {
                "schema_version": 1, "run_id": run_id, "status": "failed",
                "failure": "auth_unavailable", "arms": {},
            }

        fixtures: dict[str, Path] = {}
        homes: dict[str, Path] = {}
        digests: dict[str, str] = {}
        for true_arm in ("baseline", "treatment"):
            fixtures[true_arm] = private_root / f"{true_arm}-fixture"
            homes[true_arm] = private_root / f"{true_arm}-home"
            digests[true_arm] = _copy_identical_fixture(fixture_source, fixtures[true_arm])
            arm_auth = homes[true_arm] / ".codex" / "auth.json"
            if not core._copy_auth(auth_snapshot, arm_auth):
                return {
                    "schema_version": 1, "run_id": run_id, "status": "failed",
                    "failure": "auth_setup_failed", "arms": {},
                }
        if digests["baseline"] != digests["treatment"]:
            raise RuntimeError("fixture parity verification failed")

        arms: dict[str, dict[str, Any]] = {}
        for true_arm in true_order:
            prompt = BASE_PROMPT + (TREATMENT_RANKING if true_arm == "treatment" else "")
            label = arm_labels[true_arm]
            arms[label] = _run_arm(
                codex=codex, model=model, reasoning_effort=reasoning_effort,
                prompt=prompt, fixture=fixtures[true_arm], home=homes[true_arm],
                timeout=timeout, allow_openrouter_key=allow_openrouter_key,
            )
            arms[label]["fixture_sha256_before"] = digests[true_arm]

        original = _safe_final_artifact(fixture_source)
        artifacts_captured: dict[str, bool] = {}
        for true_arm in ("baseline", "treatment"):
            label = arm_labels[true_arm]
            final = _safe_final_artifact(fixtures[true_arm])
            changed = final is not None and original is not None and final != original
            artifacts_captured[label] = changed
            if changed and final is not None:
                _private_write(output_dir / f"{label}-final.py", final)

        mapping = {
            "run_id": run_id,
            "arm-a": "treatment" if arm_labels["treatment"] == "arm-a" else "baseline",
            "arm-b": "treatment" if arm_labels["treatment"] == "arm-b" else "baseline",
        }
        _private_write(
            output_dir / "arm-map.json",
            (json.dumps(mapping, sort_keys=True) + "\n").encode("utf-8"),
        )
        gates_pass = all(
            result.get("cli_status") == "completed"
            and result.get("required_suite_exit") == 0
            and len(result.get("focused_test_exits", [])) >= 1
            and all(item["exit_code"] == 0 for item in result["focused_test_exits"])
            for result in arms.values()
        )
        receipt = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "completed" if gates_pass else "failed",
            "pair_order": [arm_labels[arm] for arm in true_order],
            "timeout_seconds": timeout,
            "event_limit_bytes": EVENT_LIMIT,
            "fixture_parity_sha256": digests["baseline"],
            "choice_policy": "validated output IDs only; otherwise unscored",
            "required_gate_id": "full",
            "artifacts_captured": artifacts_captured,
            "arms": arms,
        }
        _private_write(
            output_dir / "receipt.json",
            (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="authorize execution of Codex CLI arms")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="new output directory for this run's private receipts",
    )
    parser.add_argument(
        "--allow-openrouter-key", action="store_true",
        help="pass the existing OPENROUTER_API_KEY to both isolated arms",
    )
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
            allow_openrouter_key=args.allow_openrouter_key,
        )
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({"status": "failed", "failure": "runner_setup_failed"}))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
