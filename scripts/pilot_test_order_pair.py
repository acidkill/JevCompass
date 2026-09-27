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
import math
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
    "\n\nAfter your code edit, ask JevCompass to order the focused checks "
    "from the identical local test-options.json file by running exactly "
    "python -m jevcompass tests rank --input test-options.json --json. "
    "The rank command only orders checks; it does not execute them. Follow "
    "its focused order by running one candidate, then always run the full "
    "required suite yourself. If ranking fails, choose a focused check "
    "locally and preserve the required full suite. Report the rank command "
    "exit status without replacing a test failure with success."
)
OPENROUTER_KEY_HELP = "pass the existing OPENROUTER_API_KEY to both isolated arms"
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
        # CLI JSON may represent embedded shell newlines as backslash-n/r.
        if any(marker in raw for marker in ("\n", "\r", r"\n", r"\r")):
            return None
        try:
            argv = shlex.split(raw)
        except ValueError:
            return None
    else:
        return None

    shell_names = {"bash", "/bin/bash", "/usr/bin/bash",
                   "sh", "/bin/sh", "/usr/bin/sh"}
    shell_family = {"bash", "sh", "zsh", "dash", "fish", "ksh"}
    shell_name = Path(argv[0]).name if argv else ""
    if shell_name in shell_family:
        # Only unwrap the command-only forms observed in CLI command events.
        # Parsing is lexical: never invoke a shell or evaluate its input.
        if argv[0] not in shell_names or len(argv) != 3 or argv[1] not in {"-c", "-lc"}:
            return None
        script = argv[2]
        if any(char in script for char in "$`\n\r"):
            return None
        try:
            lexer = shlex.shlex(script, posix=True, punctuation_chars="();<>|&")
            lexer.whitespace_split = True
            lexer.commenters = ""
            script_argv = list(lexer)
        except ValueError:
            return None
        if (not script_argv
                or any(token and all(char in "();<>|&" for char in token)
                       for token in script_argv)
                or any(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token)
                       for token in script_argv)):
            return None
        return script_argv
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
    return bool(argv and argv[0] in {"python", "python3", sys.executable}
                and argv[1:] == ["-m", "jevcompass", "tests", "rank", "--input",
                                  "test-options.json", "--json"])


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


def _rank_usage(payloads: list[tuple[float, str]], invocation_count: int) -> dict[str, Any]:
    """Aggregate numeric receipts only when every observed call is accounted for."""
    values = []
    for _, payload in payloads:
        try:
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError("duplicate-key")
                    result[key] = value
                return result
            data = json.loads(payload, object_pairs_hook=pairs)
            usage = data.get("usage") if isinstance(data, dict) else None
            if not isinstance(usage, dict):
                continue
            counts = [usage.get("input_tokens"), usage.get("output_tokens")]
            if any(isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 10**12
                   for n in counts):
                continue
            cost = usage.get("cost_usd")
            if cost is not None and (
                isinstance(cost, bool) or not isinstance(cost, (int, float))
                or not math.isfinite(cost) or not 0 <= cost <= 10**12
            ):
                continue
            values.append((counts[0], counts[1], cost))
        except (ValueError, TypeError):
            continue
    complete = invocation_count > 0 and len(values) == invocation_count
    return {
        "status": "not_invoked" if invocation_count == 0 else
                  "complete" if complete else "incomplete",
        "invocation_count": invocation_count,
        "input_tokens": sum(v[0] for v in values) if complete else None,
        "output_tokens": sum(v[1] for v in values) if complete else None,
        "jev_provider_cost_usd": sum(v[2] for v in values)
        if complete and all(v[2] is not None for v in values) else None,
    }


def _event_receipts(
    lines: Iterable[str], event_times: list[float], started: float,
) -> dict[str, Any]:
    pending_tests: dict[str, str] = {}
    completed_tests: list[dict[str, Any]] = []
    first_observed_focused_failure_ms: float | None = None
    first_useful_error_ms: float | None = None
    pending_ranks: dict[str, float] = {}
    rank_started_ids: set[str] = set()
    rank_completed_ids: set[str] = set()
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
            if _rank_invocation(item) and event_id not in rank_started_ids:
                rank_started_ids.add(event_id)
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
            if event_id in pending_ranks and event_id not in rank_completed_ids:
                rank_completed_ids.add(event_id)
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
        "rank_usage": _rank_usage(rank_payloads, len(rank_started_ids)),
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


def _cli_command(codex: str, model: str, reasoning_effort: str, prompt: str, *, allow_network: bool = False) -> list[str]:
    if (not SAFE_MODEL.fullmatch(model) or not SAFE_ID.fullmatch(reasoning_effort)):
        raise ValueError("model and reasoning effort must be simple identifiers")
    command = [
        codex, "-a", "never", "exec", "--json", "--ephemeral",
        "--sandbox", "workspace-write", "--skip-git-repo-check",
        "--dangerously-bypass-hook-trust", "--model", model,
        "--config", f"model_reasoning_effort={reasoning_effort}",
    ]
    if allow_network:
        command.extend(["--config", "sandbox_workspace_write.network_access=true"])
    return [*command, prompt]


def _elapsed_ms_since(started: float) -> float | None:
    """Measure a duration from a monotonic start, or retain unknown as None."""
    try:
        ended = time.monotonic()
        elapsed = (ended - started) * 1000
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(elapsed) or elapsed < 0:
        return None
    return round(elapsed, 2)


def _known_nonnegative_finite_ms(value: Any) -> float | None:
    """Normalize a supplied duration without turning malformed/huge values into zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric) or numeric < 0:
        return None
    return round(numeric, 2)


def _pair_timing_receipt(
    pair_started: float, *,
    shared_setup_ms: float | None,
    shared_auth_preparation_ms: float | None,
    fixture_parity_check_ms: float | None,
    per_arm_fixture_auth_preparation_ms: dict[str, float | None],
    per_arm_run_arm_ms: dict[str, float | None],
    per_arm_validation_status: dict[str, str | None],
    per_arm_validation_ms: dict[str, float | None],
    post_run_gate_validation_ms: float | None,
) -> dict[str, Any]:
    """Expose only measured monotonic durations and explicit unavailable validation."""
    return {
        "clock": "time.monotonic",
        "total_pair_elapsed_ms": _elapsed_ms_since(pair_started),
        "shared_setup_elapsed_ms": shared_setup_ms,
        "shared_auth_preparation_elapsed_ms": shared_auth_preparation_ms,
        "fixture_parity_check_elapsed_ms": fixture_parity_check_ms,
        "per_arm_fixture_auth_preparation_elapsed_ms": {
            label: per_arm_fixture_auth_preparation_ms.get(label)
            for label in ("arm-a", "arm-b")
        },
        "per_arm_run_arm_elapsed_ms": {
            label: per_arm_run_arm_ms.get(label) for label in ("arm-a", "arm-b")
        },
        "per_arm_independent_validation_status": {
            label: per_arm_validation_status.get(label) for label in ("arm-a", "arm-b")
        },
        "per_arm_independent_validation_elapsed_ms": {
            label: per_arm_validation_ms.get(label) for label in ("arm-a", "arm-b")
        },
        "post_run_gate_validation_elapsed_ms": post_run_gate_validation_ms,
        "independent_validation_timing_scope": (
            "Any validation performed inside _run_arm is included in that arm's run-arm "
            "duration; separate validation duration is reported only when the runner returns it."
        ),
        "total_elapsed_scope": (
            "base runner through gate evaluation before receipt serialization; excludes "
            "wrapper postprocessing and serialization"
        ),
    }


def _run_arm(
    *, codex: str, model: str, reasoning_effort: str, prompt: str,
    fixture: Path, home: Path, timeout: int, allow_openrouter_key: bool,
    max_tokens: int | None = None,
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
            _cli_command(codex, model, reasoning_effort, prompt,
                         allow_network=allow_openrouter_key),
            cwd=fixture, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        collect_kwargs = {"started": started, "timeout": timeout}
        if max_tokens is not None:
            collect_kwargs["max_tokens"] = max_tokens
        lines, times, failure = core._collect_events(process, **collect_kwargs)
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
    baseline_prompt: str | None = None,
    treatment_prompt_suffix: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Run both isolated arms and write private, redacted receipts/artifact."""
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if max_tokens is not None and (
        isinstance(max_tokens, bool) or not isinstance(max_tokens, int)
        or not 1 <= max_tokens <= core.MAX_EVENT_TOKEN_BUDGET
    ):
        raise ValueError(
            f"max_tokens must be between 1 and {core.MAX_EVENT_TOKEN_BUDGET}"
        )
    if baseline_prompt is not None and (
        not isinstance(baseline_prompt, str) or not baseline_prompt.strip()
        or len(baseline_prompt.encode("utf-8")) > 32 * 1024
    ):
        raise ValueError("baseline prompt must be nonempty and bounded")
    if treatment_prompt_suffix is not None and (
        not isinstance(treatment_prompt_suffix, str) or not treatment_prompt_suffix.strip()
        or len(treatment_prompt_suffix.encode("utf-8")) > 16 * 1024
    ):
        raise ValueError("treatment prompt suffix must be nonempty and bounded")
    base_prompt = BASE_PROMPT if baseline_prompt is None else baseline_prompt
    treatment_suffix = (
        TREATMENT_RANKING if treatment_prompt_suffix is None
        else treatment_prompt_suffix
    )
    if not fixture_source.is_dir():
        raise FileNotFoundError("synthetic coding fixture is unavailable")
    if allow_openrouter_key and not os.environ.get("OPENROUTER_API_KEY"):
        raise ValueError("the opted-in API key is unavailable")
    pair_started = time.monotonic()
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
    shared_setup_ms: float | None = None
    shared_auth_preparation_ms: float | None = None
    fixture_parity_check_ms: float | None = None
    per_arm_fixture_auth_preparation_ms: dict[str, float | None] = {
        "arm-a": None, "arm-b": None,
    }
    per_arm_run_arm_ms: dict[str, float | None] = {"arm-a": None, "arm-b": None}
    per_arm_validation_status: dict[str, str | None] = {"arm-a": None, "arm-b": None}
    per_arm_validation_ms: dict[str, float | None] = {"arm-a": None, "arm-b": None}
    post_run_gate_validation_ms: float | None = None
    shared_setup_started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="jev-test-order-pair-") as temporary:
        private_root = Path(temporary)
        os.chmod(private_root, 0o700)
        shared_setup_ms = _elapsed_ms_since(shared_setup_started)
        auth_snapshot = private_root / "auth-snapshot.json"
        shared_auth_started = time.monotonic()
        try:
            auth_ok = core._copy_auth(source_auth, auth_snapshot)
        except OSError:
            auth_ok = False
        shared_auth_preparation_ms = _elapsed_ms_since(shared_auth_started)
        if not auth_ok:
            receipt = {
                "schema_version": 1, "run_id": run_id, "status": "failed",
                "failure": "auth_unavailable", "arms": {},
                "timing": _pair_timing_receipt(
                    pair_started, shared_setup_ms=shared_setup_ms,
                    shared_auth_preparation_ms=shared_auth_preparation_ms,
                    fixture_parity_check_ms=fixture_parity_check_ms,
                    per_arm_fixture_auth_preparation_ms=per_arm_fixture_auth_preparation_ms,
                    per_arm_run_arm_ms=per_arm_run_arm_ms,
                    per_arm_validation_status=per_arm_validation_status,
                    per_arm_validation_ms=per_arm_validation_ms,
                    post_run_gate_validation_ms=post_run_gate_validation_ms,
                ),
            }
            if max_tokens is not None:
                receipt["max_tokens_per_arm"] = max_tokens
                receipt["token_budget_usage_limitation"] = (
                    "The cap is checked only when valid completed-turn usage is observed; "
                    "it is not a provider per-request hard cap. If completed-turn usage is "
                    "missing or malformed, budget usage is unknown."
                )
            return receipt

        fixtures: dict[str, Path] = {}
        homes: dict[str, Path] = {}
        digests: dict[str, str] = {}
        for true_arm in ("baseline", "treatment"):
            label = arm_labels[true_arm]
            arm_prep_started = time.monotonic()
            fixtures[true_arm] = private_root / f"{true_arm}-fixture"
            homes[true_arm] = private_root / f"{true_arm}-home"
            digests[true_arm] = _copy_identical_fixture(fixture_source, fixtures[true_arm])
            arm_auth = homes[true_arm] / ".codex" / "auth.json"
            auth_ready = core._copy_auth(auth_snapshot, arm_auth)
            per_arm_fixture_auth_preparation_ms[label] = _elapsed_ms_since(arm_prep_started)
            if not auth_ready:
                receipt = {
                    "schema_version": 1, "run_id": run_id, "status": "failed",
                    "failure": "auth_setup_failed", "arms": {},
                    "timing": _pair_timing_receipt(
                        pair_started, shared_setup_ms=shared_setup_ms,
                        shared_auth_preparation_ms=shared_auth_preparation_ms,
                        fixture_parity_check_ms=fixture_parity_check_ms,
                        per_arm_fixture_auth_preparation_ms=per_arm_fixture_auth_preparation_ms,
                        per_arm_run_arm_ms=per_arm_run_arm_ms,
                        per_arm_validation_status=per_arm_validation_status,
                        per_arm_validation_ms=per_arm_validation_ms,
                        post_run_gate_validation_ms=post_run_gate_validation_ms,
                    ),
                }
                if max_tokens is not None:
                    receipt["max_tokens_per_arm"] = max_tokens
                    receipt["token_budget_usage_limitation"] = (
                        "The cap is checked only when valid completed-turn usage is observed; "
                        "it is not a provider per-request hard cap. If completed-turn usage is "
                        "missing or malformed, budget usage is unknown."
                    )
                return receipt
        parity_started = time.monotonic()
        parity_verified = digests["baseline"] == digests["treatment"]
        fixture_parity_check_ms = _elapsed_ms_since(parity_started)
        if not parity_verified:
            raise RuntimeError("fixture parity verification failed")

        arms: dict[str, dict[str, Any]] = {}
        for true_arm in true_order:
            prompt = base_prompt + (treatment_suffix if true_arm == "treatment" else "")
            label = arm_labels[true_arm]
            arm_kwargs = dict(
                codex=codex, model=model, reasoning_effort=reasoning_effort,
                prompt=prompt, fixture=fixtures[true_arm], home=homes[true_arm],
                timeout=timeout, allow_openrouter_key=allow_openrouter_key,
            )
            if max_tokens is not None:
                arm_kwargs["max_tokens"] = max_tokens
            arm_run_started = time.monotonic()
            arms[label] = _run_arm(**arm_kwargs)
            per_arm_run_arm_ms[label] = _elapsed_ms_since(arm_run_started)
            arms[label]["fixture_sha256_before"] = digests[true_arm]
            raw_validation_ms = arms[label].get(
                "independent_validation_elapsed_ms",
                arms[label].get("independent_validation_ms"),
            )
            per_arm_validation_ms[label] = _known_nonnegative_finite_ms(raw_validation_ms)
            per_arm_validation_status[label] = (
                "included_in_run_arm_elapsed"
                if ("independent_validation" in arms[label]
                    or "independent_contract_validation" in arms[label]
                    or per_arm_validation_ms[label] is not None)
                else "not_reported_by_runner"
            )
            completion_ms = arms[label].get("completion_ms")
            completion_observed = _known_nonnegative_finite_ms(completion_ms) is not None
            arms[label]["timing"] = {
                "fixture_auth_preparation_elapsed_ms": (
                    per_arm_fixture_auth_preparation_ms[label]
                ),
                "run_arm_elapsed_ms": per_arm_run_arm_ms[label],
                "agent_completion_status": (
                    "observed" if completion_observed else "unavailable"
                ),
                "independent_validation_status": per_arm_validation_status[label],
                "independent_validation_elapsed_ms": per_arm_validation_ms[label],
            }
            if max_tokens is not None:
                arms[label]["max_tokens_per_arm"] = max_tokens
                usage_status = arms[label].get("token_usage_status")
                arms[label]["token_budget_status"] = (
                    "exceeded" if arms[label].get("failure") == "token_budget_exceeded"
                    else "within_observed_budget" if usage_status == "available"
                    else "usage_unknown"
                )

        post_run_validation_started = time.monotonic()
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
        post_run_gate_validation_ms = _elapsed_ms_since(post_run_validation_started)
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
            "timing": _pair_timing_receipt(
                pair_started, shared_setup_ms=shared_setup_ms,
                shared_auth_preparation_ms=shared_auth_preparation_ms,
                fixture_parity_check_ms=fixture_parity_check_ms,
                per_arm_fixture_auth_preparation_ms=per_arm_fixture_auth_preparation_ms,
                per_arm_run_arm_ms=per_arm_run_arm_ms,
                per_arm_validation_status=per_arm_validation_status,
                per_arm_validation_ms=per_arm_validation_ms,
                post_run_gate_validation_ms=post_run_gate_validation_ms,
            ),
        }
        if max_tokens is not None:
            receipt["max_tokens_per_arm"] = max_tokens
            receipt["token_budget_usage_limitation"] = (
                "The cap is checked only when valid completed-turn usage is observed; "
                "it is not a provider per-request hard cap. If completed-turn usage is "
                "missing or malformed, budget usage is unknown."
            )
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
    parser.add_argument(
        "--max-tokens", type=int,
        help=("optional per-arm observed usage ceiling; enforced after completed-turn "
              "usage events, not a provider per-request hard cap"),
    )
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="new output directory for this run's private receipts",
    )
    parser.add_argument(
        "--allow-openrouter-key", action="store_true",
        help=OPENROUTER_KEY_HELP,
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
            max_tokens=args.max_tokens,
        )
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({"status": "failed", "failure": "runner_setup_failed"}))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
