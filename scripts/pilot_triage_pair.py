#!/usr/bin/env python3
"""Run a bounded, randomized synthetic matched pair for coding triage.

Both arms receive the fixture README, identical fixture copies, isolated copies
of the same auth profile, and the same explicit Codex settings. The treatment
alone receives an instruction to request allowlisted triage after its initial
focused test failure. Event receipts distinguish subsequent checks from proof
of edit order; pair completion also requires each final artifact to differ from
the seed, checked separately. Raw events, prompts, and source text stay in memory.
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
from pilot_receipts import parse_choice_receipt, parse_codex_json_events


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ambiguous_import_triage"
CHANGED_FILE = "parcelcache/api.py"
MAX_ARTIFACT_BYTES = 64 * 1024
MAX_TRIAGE_OUTPUT_BYTES = 64 * 1024
MAX_TIMEOUT = core.MAX_TIMEOUT
DEFAULT_TIMEOUT = min(180, MAX_TIMEOUT)
EVENT_LIMIT = core.MAX_EVENT_BYTES

FOCUSED_COMMAND = "python -m unittest discover -s tests -p 'test_store.py' -v"
REQUIRED_COMMAND = "python -m unittest discover -s tests -v"
PROBE_CODE = (
    'import importlib.util; '
    'package = importlib.util.find_spec("parcelcache"); '
    'target = importlib.util.find_spec("parcelcache.codec"); '
    'replacement = importlib.util.find_spec("parcelcache.wire"); '
    'print("package_present" if package else "package_absent"); '
    'print("target_module_absent" if target is None else "target_module_present"); '
    'print("replacement_module_present" if replacement else "replacement_module_absent")'
)
DISCRIMINATOR_COMMAND = f"python -c '{PROBE_CODE}'"
DISCRIMINATOR_OUTPUT = (
    "package_present",
    "target_module_absent",
    "replacement_module_present",
)
TRIAGE_PREFIX = (
    "python -m jevcompass triage --exit-code",
)
TRIAGE_SUFFIX = (
    "--kind import --hypothesis import_module_missing "
    "--hypothesis import_path_changed "
    "--import-observation package_present "
    "--import-observation target_module_absent "
    "--import-observation replacement_module_present --json"
)
TRIAGE_IDS = ("import_path_changed",)

# The fixture README is the common task prompt. Treatment-only text is appended
# as the experimental intervention, without including evaluator expectations.
BASE_PROMPT = (FIXTURE / "README.md").read_text(encoding="utf-8")
TREATMENT_TRIAGE = (
    "\n\nAfter the initial focused test command completes with exit code 1, run "
    "this exact read-only discriminator from the fixture root: "
    f"{DISCRIMINATOR_COMMAND}. Do not invoke triage unless the command exits 0 "
    f"and its complete output is exactly {' | '.join(DISCRIMINATOR_OUTPUT)}. "
    "If and only if those observations are "
    "verified, run exactly "
    "python -m jevcompass triage --exit-code 1 --kind import "
    "--hypothesis import_module_missing --hypothesis import_path_changed "
    "--import-observation package_present "
    "--import-observation target_module_absent "
    "--import-observation replacement_module_present --json. The CLI only "
    "recommends diagnostic steps; it does not run tests or diagnostics. "
    "Continue with the minimal source fix, then run the exact focused and "
    "required suites from the task."
)
_FAILED_TEST = re.compile(
    r"ERROR:\s*test_store\s*\(unittest\.loader\._FailedTest\.test_store\)"
)
_MISSING_CODEC = re.compile(
    r"ModuleNotFoundError:\s*No module named\s+['\"]parcelcache\.codec['\"]"
)


def _triage_command(exit_code: int) -> list[str]:
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code == 0:
        raise ValueError("triage requires an observed nonzero focused exit")
    return shlex.split(
        f"{TRIAGE_PREFIX[0]} {exit_code} {TRIAGE_SUFFIX}"
    )


def _is_focused(item: dict[str, Any]) -> bool:
    return common._command_argv(item) == shlex.split(FOCUSED_COMMAND)


def _is_full(item: dict[str, Any]) -> bool:
    return common._command_argv(item) == shlex.split(REQUIRED_COMMAND)


def _is_discriminator(item: dict[str, Any]) -> bool:
    return common._command_argv(item) == shlex.split(DISCRIMINATOR_COMMAND)


def _matches_discriminator_output(output: Any) -> bool:
    return (
        isinstance(output, str)
        and output.splitlines() == list(DISCRIMINATOR_OUTPUT)
    )


def _is_triage(item: dict[str, Any], original_exit: int | None) -> bool:
    if original_exit is None or original_exit == 0:
        return False
    return common._command_argv(item) == _triage_command(original_exit)


def _matches_useful_error(output: Any) -> bool:
    return (
        isinstance(output, str)
        and _FAILED_TEST.search(output) is not None
        and _MISSING_CODEC.search(output) is not None
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


def _validated_triage(payload: Any, original_exit: int) -> dict[str, Any]:
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_TRIAGE_OUTPUT_BYTES:
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
        or parsed.get("candidate_ids") != ["import_path_changed"]
        or parsed.get("observed_exit_status") != original_exit
        or value.get("test_failed") is not True
    ):
        return {"status": "unscored", "candidate_ids": []}
    return parsed


def _event_receipts(
    lines: Iterable[str], event_times: list[float], started: float,
) -> dict[str, Any]:
    pending_focused: dict[str, int] = {}
    pending_full: dict[str, int] = {}
    pending_discriminators: dict[str, bool] = {}
    pending_triage: dict[str, tuple[bool, bool]] = {}
    first_focused_exit: int | None = None
    first_focused_index: int | None = None
    first_useful_error_ms: float | None = None
    useful_error_match = False
    discriminator_invocation_observed = False
    discriminator_successful = False
    first_successful_discriminator_ms: float | None = None
    subsequent_focused_exits: list[int] = []
    full_exits: list[int] = []
    triage_exit: int | None = None
    triage_choice = {"status": "unscored", "candidate_ids": []}
    triage_output_status = "not_invoked"
    triage_invocation_observed = False
    triage_invalid_invocation_observed = False
    triage_after_discriminator = False
    focused_count = 0
    full_count = 0

    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        item = common._item(event)
        if item is None:
            continue
        raw_id = item.get("id")
        event_id = raw_id if isinstance(raw_id, str) and 0 < len(raw_id) <= 128 else None
        if event_type == "item.started" and event_id:
            if _is_focused(item):
                focused_count += 1
                pending_focused[event_id] = index
            if _is_full(item):
                full_count += 1
                pending_full[event_id] = index
            if _is_discriminator(item):
                discriminator_invocation_observed = True
                pending_discriminators[event_id] = (
                    first_focused_exit == 1 and useful_error_match
                )
            argv = common._command_argv(item)
            if argv and "jevcompass" in argv:
                triage_invocation_observed = True
                exact_command = _is_triage(item, first_focused_exit)
                accepted = (
                    exact_command
                    and first_focused_exit == 1
                    and useful_error_match
                    and discriminator_successful
                )
                triage_after_discriminator = triage_after_discriminator or accepted
                triage_invalid_invocation_observed = (
                    triage_invalid_invocation_observed or not accepted
                )
                triage_output_status = (
                    "captured" if accepted else "out_of_order_or_invalid_command"
                )
                pending_triage[event_id] = (accepted, exact_command)
        elif event_type == "item.completed" and event_id:
            if event_id in pending_focused:
                pending_focused.pop(event_id)
                code = item.get("exit_code")
                if isinstance(code, int) and not isinstance(code, bool):
                    if first_focused_exit is None:
                        first_focused_exit = code
                        first_focused_index = index
                        output = item.get("aggregated_output")
                        if not isinstance(output, str):
                            output = item.get("output")
                        observed = event_times[index] if index < len(event_times) else None
                        useful_error_match = code == 1 and _matches_useful_error(output)
                        if useful_error_match and observed is not None:
                            first_useful_error_ms = round(max(0.0, (observed - started) * 1000), 2)
                    else:
                        subsequent_focused_exits.append(code)
            if event_id in pending_full:
                pending_full.pop(event_id)
                code = item.get("exit_code")
                if isinstance(code, int) and not isinstance(code, bool):
                    full_exits.append(code)
            if event_id in pending_discriminators:
                eligible = pending_discriminators.pop(event_id)
                code = item.get("exit_code")
                output = item.get("aggregated_output")
                if not isinstance(output, str):
                    output = item.get("output")
                successful = (
                    eligible
                    and isinstance(code, int)
                    and not isinstance(code, bool)
                    and code == 0
                    and _matches_discriminator_output(output)
                )
                if successful:
                    discriminator_successful = True
                    observed = event_times[index] if index < len(event_times) else None
                    if first_successful_discriminator_ms is None and observed is not None:
                        first_successful_discriminator_ms = round(
                            max(0.0, (observed - started) * 1000), 2,
                        )
            if event_id in pending_triage:
                accepted, exact_command = pending_triage.pop(event_id)
                code = item.get("exit_code")
                triage_exit = code if isinstance(code, int) and not isinstance(code, bool) else None
                output = item.get("aggregated_output")
                if not isinstance(output, str):
                    output = item.get("output")
                if accepted and exact_command and triage_exit == 0:
                    triage_choice = _validated_triage(output, first_focused_exit or 0)
                    triage_output_status = (
                        "valid_local_resolution"
                        if triage_choice["status"] == "no-remote-choice"
                        and triage_choice["candidate_ids"] == ["import_path_changed"]
                        else "invalid_output"
                    )
                elif exact_command:
                    triage_output_status = "invalid_output"

    return {
        "initial_focused_invocation_observed": first_focused_index is not None,
        "initial_focused_exit_code": first_focused_exit,
        "initial_useful_error_match": useful_error_match,
        "initial_useful_error_ms": first_useful_error_ms,
        "discriminator_invocation_observed": discriminator_invocation_observed,
        "discriminator_successful": discriminator_successful,
        "first_successful_discriminator_ms": first_successful_discriminator_ms,
        "subsequent_focused_invocation_observed": bool(subsequent_focused_exits),
        "subsequent_focused_exit_code": subsequent_focused_exits[-1] if subsequent_focused_exits else None,
        "focused_invocation_count": focused_count,
        "required_suite_invocation_observed": full_count > 0,
        "required_suite_exit": full_exits[-1] if full_exits else None,
        "triage_cli_invocation_observed": triage_invocation_observed,
        "triage_invalid_invocation_observed": triage_invalid_invocation_observed,
        "triage_after_discriminator": triage_after_discriminator,
        "triage_cli_exit_code": triage_exit,
        "triage_output_status": triage_output_status,
        "triage": triage_choice,
    }


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
            common._cli_command(
                codex, model, reasoning_effort, prompt,
                allow_network=allow_openrouter_key,
            ),
            cwd=fixture, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        lines, times, failure = core._collect_events(
            process, started=started, timeout=timeout, preserve_on_failure=True,
        )
    except OSError:
        return _empty_arm("codex_unavailable")
    wall_ms = round((time.monotonic() - started) * 1000, 2)
    observed = _event_receipts(lines, times, started)
    counter = parse_codex_json_events(
        lines, started_at=started, ended_at=time.monotonic(), event_times=times,
    )
    result = {
        "cli_status": "completed" if failure is None and process.returncode == 0 else "failed",
        "failure": failure,
        "cli_exit_code": process.returncode if failure is None else None,
        "completion_ms": wall_ms if wall_ms <= MAX_TIMEOUT * 1000 else None,
        "event_count": len(lines),
        "token_usage_status": counter["usage_status"],
        "token_usage": counter["token_usage"],
        "billing_estimate": None,
        **observed,
    }
    return result


def _empty_arm(failure: str) -> dict[str, Any]:
    return {
        "cli_status": "failed",
        "failure": failure,
        "cli_exit_code": None,
        "completion_ms": None,
        "event_count": 0,
        "token_usage_status": "unscored",
        "token_usage": None,
        "billing_estimate": None,
        "initial_focused_invocation_observed": False,
        "initial_focused_exit_code": None,
        "initial_useful_error_match": False,
        "initial_useful_error_ms": None,
        "discriminator_invocation_observed": False,
        "discriminator_successful": False,
        "first_successful_discriminator_ms": None,
        "subsequent_focused_invocation_observed": False,
        "subsequent_focused_exit_code": None,
        "focused_invocation_count": 0,
        "required_suite_invocation_observed": False,
        "required_suite_exit": None,
        "triage_cli_invocation_observed": False,
        "triage_invalid_invocation_observed": False,
        "triage_after_discriminator": False,
        "triage_cli_exit_code": None,
        "triage_output_status": "not_invoked",
        "triage": {"status": "unscored", "candidate_ids": []},
    }


def _safe_final_artifact(fixture: Path) -> bytes | None:
    path = fixture / CHANGED_FILE
    try:
        if path.is_symlink() or not path.is_file():
            return None
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
            return None
        content = path.read_bytes()
        return content if len(content) <= MAX_ARTIFACT_BYTES else None
    except OSError:
        return None


def _arm_passed(
    arm: dict[str, Any], *, treatment: bool, artifact_changed: bool,
) -> bool:
    # Event timing proves the discriminator precedes treatment triage, while
    # the final artifact comparison remains independent of test timing.
    process_gate = (
        arm.get("cli_status") == "completed"
        and arm.get("initial_focused_invocation_observed") is True
        and arm.get("initial_focused_exit_code") == 1
        and arm.get("initial_useful_error_match") is True
        and arm.get("subsequent_focused_invocation_observed") is True
        and arm.get("subsequent_focused_exit_code") == 0
        and arm.get("required_suite_invocation_observed") is True
        and arm.get("required_suite_exit") == 0
    )
    if not process_gate or not artifact_changed:
        return False
    if not treatment:
        # Equivalent read-only diagnostics are allowed in the baseline task;
        # the strict probe detector is treatment-specific and can be unscored.
        return not arm.get("triage_cli_invocation_observed")
    return (
        arm.get("discriminator_successful") is True
        and arm.get("triage_after_discriminator") is True
        and arm.get("triage_invalid_invocation_observed") is False
        and arm.get("triage_cli_exit_code") == 0
        and arm.get("triage_output_status") == "valid_local_resolution"
        and arm.get("triage", {}).get("status") == "no-remote-choice"
        and arm.get("triage", {}).get("candidate_ids") == ["import_path_changed"]
        and arm.get("triage", {}).get("observed_exit_status")
        == arm.get("initial_focused_exit_code")
    )


def run_pair(
    *, codex: str, model: str, reasoning_effort: str, timeout: int = DEFAULT_TIMEOUT,
    seed: int | None = None, output_dir: Path, fixture_source: Path = FIXTURE,
    allow_openrouter_key: bool = False,
) -> dict[str, Any]:
    """Run both isolated arms and write private redacted receipts/artifacts."""
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if not fixture_source.is_dir():
        raise FileNotFoundError("synthetic triage fixture is unavailable")
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

    with tempfile.TemporaryDirectory(prefix="jev-triage-pair-") as temporary:
        private_root = Path(temporary)
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
        for true_arm in ("baseline", "treatment"):
            fixtures[true_arm] = private_root / f"{true_arm}-fixture"
            homes[true_arm] = private_root / f"{true_arm}-home"
            digests[true_arm] = common._copy_identical_fixture(fixture_source, fixtures[true_arm])
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
        for true_arm in true_order:
            label = arm_labels[true_arm]
            prompt = BASE_PROMPT + (TREATMENT_TRIAGE if true_arm == "treatment" else "")
            arms[label] = _run_arm(
                codex=codex, model=model, reasoning_effort=reasoning_effort,
                prompt=prompt, fixture=fixtures[true_arm], home=homes[true_arm],
                timeout=timeout, allow_openrouter_key=allow_openrouter_key,
            )
            arms[label]["fixture_sha256_before"] = digests[true_arm]

        original = _safe_final_artifact(fixture_source)
        artifacts: dict[str, bool] = {}
        for true_arm in ("baseline", "treatment"):
            label = arm_labels[true_arm]
            final = _safe_final_artifact(fixtures[true_arm])
            changed = final is not None and original is not None and final != original
            artifacts[label] = changed
            if changed and final is not None:
                common._private_write(output_dir / f"{label}-source.bin", final)

        mapping = {
            "run_id": run_id,
            "arm-a": "treatment" if arm_labels["treatment"] == "arm-a" else "baseline",
            "arm-b": "treatment" if arm_labels["treatment"] == "arm-b" else "baseline",
        }
        common._private_write(
            output_dir / "arm-map.json",
            (json.dumps(mapping, sort_keys=True) + "\n").encode("utf-8"),
        )
        passed = all(
            _arm_passed(
                arm, treatment=mapping[label] == "treatment",
                artifact_changed=artifacts[label],
            )
            for label, arm in arms.items()
        )
        receipt = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "completed" if passed else "failed",
            "pair_order": [arm_labels[arm] for arm in true_order],
            "timeout_seconds": timeout,
            "event_limit_bytes": EVENT_LIMIT,
            "fixture_parity_sha256": digests["baseline"],
            "choice_policy": "validated triage status and enum IDs only; otherwise unscored",
            "required_gate_id": "full",
            "artifacts_captured": artifacts,
            "edit_order_note": (
                "focused reruns are observed after the initial failure; final source changes "
                "are checked separately and do not prove edit-before-test order"
            ),
            "arms": arms,
        }
        common._private_write(
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
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="new output directory for this run's private receipts")
    parser.add_argument("--allow-openrouter-key", action="store_true",
                        help="pass the existing OPENROUTER_API_KEY equally to both isolated arms")
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
