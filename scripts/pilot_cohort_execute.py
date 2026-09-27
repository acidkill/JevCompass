#!/usr/bin/env python3
"""Freeze and, only with --run, execute the preregistered JevCompass cohorts.

Default invocation writes an offline manifest. Each model-backed pair runs in
its own process; the coordinator stores only redacted receipts and arm maps.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT.parent
sys.path.insert(0, str(SCRIPT_DIR))
import pilot_cohort_manifest as inventory  # noqa: E402

CASE_FILES: dict[str, tuple[str, ...]] = {
    "pretask_strategy": (
        "scripts/pilot_pretask_strategy_pair.py", "scripts/pilot_strategy_pair.py",
        "scripts/pilot_test_order_pair.py", "scripts/pilot_cli_core.py",
        "scripts/pilot_receipts.py", "tests/test_pilot_dependency_strategy_pair.py",
        "tests/fixtures/coding_test_order/CONTRACT.md",
        "tests/fixtures/coding_test_order/TESTING.md",
        "tests/fixtures/coding_test_order/parcelquote/__init__.py",
        "tests/fixtures/coding_test_order/parcelquote/__main__.py",
        "tests/fixtures/coding_test_order/parcelquote/quote.py",
        "tests/fixtures/coding_test_order/test-options.json",
        "tests/fixtures/coding_test_order/tests/test_contract_cli.py",
        "tests/fixtures/coding_test_order/tests/test_unit_quote.py",
    ),
    "post_change_test_order": (
        "scripts/pilot_target_coverage_test_order_pair.py",
        "scripts/pilot_cross_layer_test_order_pair.py",
        "scripts/pilot_test_order_pair.py", "scripts/pilot_cli_core.py",
        "scripts/pilot_receipts.py",
        "tests/test_pilot_target_coverage_test_order_pair.py",
        "tests/test_pilot_cross_layer_test_order_pair.py",
        "tests/test_cross_layer_test_order_fixture.py",
        "tests/evaluation/cross_layer_decision_protocol.md",
        "tests/fixtures/cross_layer_test_order/README.md",
        "tests/fixtures/cross_layer_test_order/CONTRACT.md",
        "tests/fixtures/cross_layer_test_order/TESTING.md",
        "tests/fixtures/cross_layer_test_order/checkout/__init__.py",
        "tests/fixtures/cross_layer_test_order/checkout/__main__.py",
        "tests/fixtures/cross_layer_test_order/checkout/service.py",
        "tests/fixtures/cross_layer_test_order/runtime-samples.json",
        "tests/fixtures/cross_layer_test_order/test-options.json",
        "tests/fixtures/cross_layer_test_order/tests/test_integration_checkout.py",
        "tests/fixtures/cross_layer_test_order/tests/test_unit_checkout.py",
    ),
    "ambiguous_failure_triage": (
        "scripts/pilot_ambiguous_timeout_pair.py",
        "scripts/pilot_condition_timeout_pair.py",
        "scripts/pilot_cli_core.py", "scripts/pilot_receipts.py",
        "tests/test_pilot_ambiguous_timeout_pair.py",
        "tests/test_pilot_condition_timeout_pair.py",
        "tests/test_condition_timeout_triage_fixture.py",
        "tests/fixtures/condition_timeout_triage/CONTRACT.md",
        "tests/fixtures/condition_timeout_triage/README.md",
        "tests/fixtures/condition_timeout_triage/inbox.py",
        "tests/fixtures/condition_timeout_triage/tests/test_inbox.py",
    ),
}
PROTOCOL_FILES = ("BENCHMARK_PROTOCOL.md", "scripts/pilot_cohort_manifest.py", str(SCRIPT.relative_to(ROOT)))
CASE_ORDER = ("pretask_strategy", "post_change_test_order", "ambiguous_failure_triage")
# The WIP timeout wrapper's caller/route is not accepted as a remote ambiguity case.
TRIAGE_REMOTE_ROUTE_VERIFIED = False
IDENTIFIER = re.compile(r"[A-Za-z0-9._:+/@-]{1,160}\Z")
SAFE_VERSION = re.compile(r"[A-Za-z0-9._+ -]{1,160}\Z")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(root: Path, relative: str) -> str | None:
    path = root / relative
    if path.is_symlink() or not path.is_file():
        return None
    return _sha(path.read_bytes())


def _git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=True,
        )
        return completed.stdout.decode("utf-8").strip()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None


def _run_probe(case: str, *, root: Path = ROOT) -> dict[str, Any]:
    command = [sys.executable, str(SCRIPT), "--_probe-case", case]
    try:
        result = subprocess.run(
            command, cwd=root, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unavailable", "reason": "probe_failed"}
    if result.returncode != 0 or len(result.stdout) > 32 * 1024:
        return {"status": "unavailable", "reason": "probe_failed"}
    try:
        value = json.loads(result.stdout)
    except (UnicodeError, ValueError):
        return {"status": "unavailable", "reason": "probe_invalid"}
    return value if isinstance(value, dict) else {"status": "unavailable", "reason": "probe_invalid"}


def _probe_case(case: str) -> dict[str, Any]:
    sys.path.insert(0, str(SCRIPT_DIR))
    import inspect

    functions: dict[str, Any] = {}
    max_tokens_applied: bool | None = None
    if case == "pretask_strategy":
        import pilot_pretask_strategy_pair as pre
        module = pre.engine
        prompt_parts = {
            "base_prompt": module.BASE_PROMPT,
            "treatment_strategy_marker": pre.MARKER,
            "treatment_selector_instruction": module.TREATMENT_RANKING,
            "strategy_command": pre.COMMAND,
            "strategy_actions": json.dumps(pre.ACTIONS, sort_keys=True),
        }
        functions["treatment_arm_and_independent_validation"] = inspect.getsource(pre._run_arm)
        functions["engine_runner"] = inspect.getsource(module.run_pair)
        installed_version = module.core.PUBLISHED_PILOT_VERSION
        max_tokens_applied = "max_tokens" in inspect.signature(pre.engine.run_pair).parameters
    elif case == "post_change_test_order":
        import pilot_target_coverage_test_order_pair as target
        module = target.cross_layer
        prompt_parts = {
            "base_prompt": target.BASELINE_PROMPT,
            "treatment_ranking_instruction": target.TARGET_RANKING,
        }
        functions["independent_validator"] = inspect.getsource(module._independent_final_validation)
        functions["pair_runner_and_gates"] = inspect.getsource(module.run_pair)
        installed_version = module.engine.core.PUBLISHED_PILOT_VERSION
        max_tokens_applied = "max_tokens" in inspect.signature(module.engine.run_pair).parameters
    elif case == "ambiguous_failure_triage":
        import pilot_ambiguous_timeout_pair as triage
        module = triage
        prompt_parts = {
            "base_prompt": triage.BASE_PROMPT,
            "treatment_guidance": triage.TREATMENT_GUIDANCE,
            "triage_command_prefix": triage.TRIAGE_COMMAND_PREFIX,
        }
        functions["independent_validator"] = inspect.getsource(triage._independent_after_frozen_inputs)
        functions["quality_gate"] = inspect.getsource(triage._arm_task_correct)
        run_arm_source = inspect.getsource(triage._run_arm)
        run_pair_source = inspect.getsource(triage.run_pair)
        installed_version = triage.timeout_fixture.core.PUBLISHED_PILOT_VERSION
        max_tokens_applied = "core._collect_events" in run_arm_source
    else:
        return {"status": "unavailable", "reason": "unknown_case"}

    return {
        "status": "available",
        "prompt_component_sha256": {key: _sha(
            value.encode("utf-8") if isinstance(value, str) else
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))
                                    for key, value in prompt_parts.items()},
        "validator_source_sha256": {key: _sha(value.encode("utf-8"))
                                   for key, value in functions.items()},
        "runner_declared_package_version": installed_version,
        "requested_route": (
            "supervisor_remote_selector_agents_keyless" if case == "pretask_strategy"
            else "remote_agent_cli_equal_key_environment" if case == "post_change_test_order"
            else "keyless_local_triage_unavailable_remote_stratum"
        ),
        "remote_route_verified": TRIAGE_REMOTE_ROUTE_VERIFIED if case == "ambiguous_failure_triage" else None,
        "local_route_verified": (
            "allow_openrouter_key=False" in run_arm_source
            and '"keyless_local_fallback_only"' in run_pair_source
            and '"not_attempted_keyless_profile"' in run_pair_source
        ) if case == "ambiguous_failure_triage" else None,
        "max_tokens_applied": max_tokens_applied,
    }


def _safe_model(value: Any) -> bool:
    return isinstance(value, str) and bool(IDENTIFIER.fullmatch(value))


def _codex_version(executable: str) -> str | None:
    resolved = shutil.which(executable)
    if not resolved:
        return None
    try:
        result = subprocess.run(
            [resolved, "--version"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 2048:
            return None
        text = result.stdout.decode("utf-8", errors="replace").strip()
        return text if SAFE_VERSION.fullmatch(text) else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _installed_package_version() -> str | None:
    try:
        from importlib.metadata import version
        value = version("jevcompass")
        return value if SAFE_VERSION.fullmatch(value) else None
    except Exception:
        return None


def _schedule_valid(rows: list[dict[str, Any]]) -> bool:
    return bool(rows) and all(
        isinstance(row, dict)
        and row.get("first_arm") in ("baseline", "treatment")
        and row.get("schedule_verified") is True
        for row in rows
    )


def _artifact_snapshot(root: Path, paths: tuple[str, ...]) -> dict[str, Any]:
    result = {}
    for relative in paths:
        digest = _file_sha(root, relative)
        dirty = _git(root, "status", "--porcelain", "--", relative)
        result[relative] = {
            "sha256": digest,
            "dirty": bool(dirty) if dirty is not None else True,
            "status": "missing" if digest is None else
                      "dirty" if dirty is None or dirty else "clean",
        }
    return result


def build_manifest(
    *,
    root: Path = ROOT,
    model: str,
    reasoning_effort: str,
    codex_executable: str = "codex",
    per_arm_timeout_seconds: int,
    per_arm_token_budget: int,
    total_runtime_seconds: int,
    total_agent_token_budget: int,
    codex_identity: str | None = None,
    installed_jev_version: str | None = None,
    openrouter_key_available: bool | None = None,
    probe: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    blockers: list[str] = []
    if not _safe_model(model) or not _safe_model(reasoning_effort):
        blockers.append("invalid_model_or_reasoning_effort")
    if type(per_arm_timeout_seconds) is not int or not 1 <= per_arm_timeout_seconds <= 180:
        blockers.append("invalid_per_arm_timeout")
    if type(per_arm_token_budget) is not int or per_arm_token_budget < 1:
        blockers.append("invalid_per_arm_token_budget")
    if type(total_runtime_seconds) is not int or total_runtime_seconds < 1:
        blockers.append("invalid_total_runtime_budget")
    if type(total_agent_token_budget) is not int or total_agent_token_budget < 1:
        blockers.append("invalid_total_agent_token_budget")
    if (type(per_arm_token_budget) is int and type(total_agent_token_budget) is int
            and total_agent_token_budget < per_arm_token_budget):
        blockers.append("total_token_budget_below_one_arm")
    observed_codex = codex_identity or _codex_version(codex_executable)
    observed_jev = installed_jev_version or _installed_package_version()
    resolved_codex = shutil.which(codex_executable)
    if not observed_codex:
        blockers.append("codex_identity_unavailable")
    if not resolved_codex:
        blockers.append("codex_executable_unavailable")
    if not observed_jev:
        blockers.append("installed_jev_identity_unavailable")
    key_available = (bool(os.environ.get("OPENROUTER_API_KEY"))
                     if openrouter_key_available is None else openrouter_key_available)
    if type(key_available) is not bool:
        blockers.append("invalid_key_presence_observation")

    revision = _git(root, "rev-parse", "HEAD")
    if revision is None:
        blockers.append("git_revision_unavailable")
    schedules = {
        "feasibility": inventory._schedule(inventory.FEASIBILITY),
        "main": inventory._schedule(inventory.MAIN),
    }
    if not _schedule_valid(schedules["feasibility"] + schedules["main"]):
        blockers.append("seed_order_schedule_mismatch")

    probe_case = probe or (lambda case: _run_probe(case, root=root))
    cases: dict[str, Any] = {}
    for case in CASE_ORDER:
        files = CASE_FILES[case]
        snapshot = _artifact_snapshot(root, files)
        file_blockers = [f"artifact_{value['status']}:{relative}"
                         for relative, value in snapshot.items()
                         if value["status"] != "clean"]
        inspected = probe_case(case)
        # Local-only timeout advice is a distinct stratum; this cohort's
        # ambiguous-remote triage contract remains unavailable pending review.
        route_ready = case != "ambiguous_failure_triage"
        reasons = list(file_blockers)
        if inspected.get("status") != "available":
            reasons.append("runner_probe_unavailable")
        if not route_ready:
            reasons.append("remote_triage_route_unverified")
        if case in ("pretask_strategy", "post_change_test_order") and not key_available:
            reasons.append("requested_remote_route_key_unavailable")
        case_status = "ready" if not reasons else "unavailable"
        cases[case] = {
            "status": case_status,
            "scope": ("repeated_task_descriptive_only" if case == "pretask_strategy"
                      else "post_change_coverage" if case == "post_change_test_order"
                      else "remote_stratum_unavailable_local_profile_not_pooled"),
            "artifacts": snapshot,
            "instruction_component_sha256": inspected.get("prompt_component_sha256"),
            "independent_validator_source_sha256": inspected.get("validator_source_sha256"),
            "runner_declared_package_version": inspected.get("runner_declared_package_version"),
            "requested_route": inspected.get("requested_route"),
            "route_status": (
                "ready" if case != "ambiguous_failure_triage"
                and (case not in ("pretask_strategy", "post_change_test_order") or key_available)
                else "unavailable"
            ),
            "reasons": reasons,
        }

    global_artifacts = _artifact_snapshot(root, PROTOCOL_FILES)
    for relative, artifact in global_artifacts.items():
        if artifact["status"] != "clean":
            blockers.append(f"global_artifact_{artifact['status']}:{relative}")
    ready_case_count = sum(case["status"] == "ready" for case in cases.values())
    settings_ready = not blockers
    execution_status = (
        "ready" if settings_ready and ready_case_count == len(cases)
        else "ready_partial" if settings_ready and ready_case_count > 0
        else "not_ready"
    )
    for case_id, case in cases.items():
        if case["status"] != "ready":
            blockers.extend(f"case_unavailable:{case_id}:{reason}" for reason in case["reasons"])

    return {
        "schema_version": 1,
        "protocol_version": 1,
        "execution_status": execution_status,
        "launch_authorized": False,
        "repository": {"revision": revision},
        "settings": {
            "model": model,
            "reasoning_effort": reasoning_effort,
            "codex_identity": observed_codex,
            "codex_executable": resolved_codex,
            "jev_installed_identity": observed_jev,
            "openrouter_key_available": key_available,
            "requested_routes": {
                "pretask_strategy": "supervisor_remote_selector_agents_keyless",
                "post_change_test_order": "remote_agent_cli_equal_key_environment",
                "ambiguous_failure_triage": "keyless_local_triage_unavailable_remote_stratum",
            },
            "execution_path": "isolated_codex_cli_source_checkout",
            "per_arm_timeout_seconds": per_arm_timeout_seconds,
            "per_arm_agent_token_budget": per_arm_token_budget,
            "total_runtime_seconds": total_runtime_seconds,
            "total_agent_token_budget": total_agent_token_budget,
            "token_limit_control": "runner_completed_turn_usage_monitor_not_provider_hard_cap",
            "billing": "unknown_unless_reported",
        },
        "schedules": schedules,
        "cases": cases,
        "global_artifacts": global_artifacts,
        "unavailable_slots_are_retained": True,
        "reruns_or_replacements": False,
        "completion_gates": ["initial_failure_if_fixture_requires", "focused_repair", "full_suite",
                             "independent_frozen_validation", "immutable_fixture_preserved"],
        "blockers": sorted(set(blockers)),
    }


def _current_fingerprint(manifest: dict[str, Any], root: Path = ROOT) -> list[str]:
    issues: list[str] = []
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        return ["invalid_manifest"]
    revision = _git(root, "rev-parse", "HEAD")
    if revision is None or manifest.get("repository", {}).get("revision") != revision:
        issues.append("repository_revision_changed")
    schedules = manifest.get("schedules", {})
    if (not isinstance(schedules, dict)
            or schedules.get("feasibility") != inventory._schedule(inventory.FEASIBILITY)
            or schedules.get("main") != inventory._schedule(inventory.MAIN)):
        issues.append("schedule_changed")
    cases = manifest.get("cases")
    if not isinstance(cases, dict) or set(cases) != set(CASE_ORDER):
        return issues + ["case_inventory_invalid"]
    for case in CASE_ORDER:
        record = cases[case]
        if not isinstance(record, dict) or not isinstance(record.get("artifacts"), dict):
            issues.append(f"case_artifact_inventory_invalid:{case}")
            continue
        for relative, prior in record["artifacts"].items():
            if relative not in CASE_FILES[case] or not isinstance(prior, dict):
                issues.append(f"invalid_artifact_path:{case}")
                continue
            current = _artifact_snapshot(root, (relative,))[relative]
            if current != prior:
                issues.append(f"artifact_changed:{case}:{relative}")
        if set(record["artifacts"]) != set(CASE_FILES[case]):
            issues.append(f"artifact_set_changed:{case}")
        probe = _run_probe(case, root=root)
        if probe.get("prompt_component_sha256") != record.get("instruction_component_sha256"):
            issues.append(f"instructions_changed:{case}")
        if probe.get("validator_source_sha256") != record.get("independent_validator_source_sha256"):
            issues.append(f"validator_changed:{case}")
    global_artifacts = manifest.get("global_artifacts")
    if not isinstance(global_artifacts, dict) or set(global_artifacts) != set(PROTOCOL_FILES):
        issues.append("global_artifact_inventory_invalid")
    else:
        for relative, prior in global_artifacts.items():
            if relative not in PROTOCOL_FILES or not isinstance(prior, dict):
                issues.append("invalid_global_artifact_path")
                continue
            if _artifact_snapshot(root, (relative,))[relative] != prior:
                issues.append(f"global_artifact_changed:{relative}")
            if prior.get("status") != "clean":
                issues.append(f"global_artifact_not_clean:{relative}")
    settings = manifest.get("settings")
    required = ("model", "reasoning_effort", "codex_identity", "jev_installed_identity",
                "codex_executable",
                "per_arm_timeout_seconds", "per_arm_agent_token_budget",
                "total_runtime_seconds", "total_agent_token_budget")
    if not isinstance(settings, dict) or any(settings.get(key) is None for key in required):
        issues.append("settings_incomplete")
    if isinstance(settings, dict):
        if settings.get("openrouter_key_available") is not bool(os.environ.get("OPENROUTER_API_KEY")):
            issues.append("credential_presence_changed")
        expected_routes = {
            "pretask_strategy": "supervisor_remote_selector_agents_keyless",
            "post_change_test_order": "remote_agent_cli_equal_key_environment",
            "ambiguous_failure_triage": "keyless_local_triage_unavailable_remote_stratum",
        }
        if settings.get("requested_routes") != expected_routes:
            issues.append("requested_route_changed")
        if (settings.get("requested_routes") == expected_routes
                and isinstance(manifest.get("cases"), dict)
                and manifest["cases"].get("pretask_strategy", {}).get("status") == "ready"
                and settings.get("openrouter_key_available") is not True):
            issues.append("remote_route_not_authorized_by_frozen_key_presence")
        for key in ("per_arm_timeout_seconds", "per_arm_agent_token_budget",
                    "total_runtime_seconds", "total_agent_token_budget"):
            if type(settings.get(key)) is not int or settings[key] <= 0:
                issues.append(f"invalid_setting:{key}")
        if type(settings.get("per_arm_timeout_seconds")) is int and settings["per_arm_timeout_seconds"] > 180:
            issues.append("per_arm_timeout_exceeded")
        if (type(settings.get("total_agent_token_budget")) is int
                and type(settings.get("per_arm_agent_token_budget")) is int
                and settings["total_agent_token_budget"] < settings["per_arm_agent_token_budget"]):
            issues.append("invalid_total_token_budget")
    return sorted(set(issues))


def _write_private(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError("refusing to overwrite manifest or ledger")
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("manifest or receipt unavailable")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest or receipt invalid")
    return value


def _run_case_child(case: str, *, codex: str, model: str, reasoning_effort: str,
                    timeout: int, max_tokens: int, seed: int,
                    output_dir: Path, requested_route: str) -> dict[str, Any]:
    sys.path.insert(0, str(SCRIPT_DIR))
    expected_routes = {
        "pretask_strategy": "supervisor_remote_selector_agents_keyless",
        "post_change_test_order": "remote_agent_cli_equal_key_environment",
        "ambiguous_failure_triage": "keyless_local_triage_unavailable_remote_stratum",
    }
    if requested_route != expected_routes.get(case):
        raise RuntimeError("requested_route_mismatch")
    if case == "pretask_strategy":
        import pilot_pretask_strategy_pair as pre
        return pre.engine.run_pair(
            codex=codex, model=model,
            reasoning_effort=reasoning_effort, timeout=timeout, seed=seed,
            output_dir=output_dir,
            allow_openrouter_key=requested_route == "supervisor_remote_selector_agents_keyless",
            max_tokens=max_tokens,
        )
    if case == "post_change_test_order":
        import pilot_target_coverage_test_order_pair as target
        return target.run_pair(
            codex=codex, model=model,
            reasoning_effort=reasoning_effort, timeout=timeout, seed=seed,
            output_dir=output_dir,
            allow_openrouter_key=requested_route == "remote_agent_cli_equal_key_environment",
            max_tokens=max_tokens,
        )
    if case == "ambiguous_failure_triage":
        if not TRIAGE_REMOTE_ROUTE_VERIFIED:
            raise RuntimeError("triage_route_unverified")
        import pilot_ambiguous_timeout_pair as triage
        original = triage.core._collect_events
        count = 0
        def bounded_collector(*args: Any, **kwargs: Any):
            nonlocal count
            count += 1
            kwargs["max_tokens"] = max_tokens
            return original(*args, **kwargs)
        triage.core._collect_events = bounded_collector
        try:
            result = triage.run_pair(
                codex=codex, model=model,
                reasoning_effort=reasoning_effort, timeout=timeout, seed=seed,
                output_dir=output_dir,
            )
            result["coordinator_token_monitor_calls"] = count
            result["max_tokens_per_arm"] = max_tokens
            result["token_budget_usage_limitation"] = (
                "completed-turn usage monitor; no provider-side hard cap"
            )
            receipt_path = output_dir / "receipt.json"
            if receipt_path.is_file() and not receipt_path.is_symlink():
                temporary = receipt_path.with_name("receipt.json.tmp")
                temporary.write_text(
                    json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8",
                )
                os.chmod(temporary, 0o600)
                os.replace(temporary, receipt_path)
            return result
        finally:
            triage.core._collect_events = original
    raise ValueError("unknown case")


def _child_main(argv: list[str]) -> int | None:
    if len(argv) < 2 or argv[0] not in ("--_probe-case", "--_run-pair"):
        return None
    if argv[0] == "--_probe-case":
        result = _probe_case(argv[1])
        sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
        return 0 if result.get("status") == "available" else 2
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--case", required=True)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", required=True)
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--max-tokens", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--requested-route", required=True)
    args = parser.parse_args(argv[1:])
    try:
        receipt = _run_case_child(
            args.case, codex=args.codex, model=args.model, reasoning_effort=args.reasoning_effort,
            timeout=args.timeout, max_tokens=args.max_tokens, seed=args.seed,
            output_dir=args.output_dir, requested_route=args.requested_route,
        )
        receipt_path = args.output_dir / "receipt.json"
        if not receipt_path.exists() and not receipt_path.is_symlink():
            _write_private(receipt_path, receipt)
        return 0 if receipt.get("status") == "completed" else 1
    except Exception as error:
        # Only a fixed error class is emitted; never print exceptions or prompts.
        safe = {"schema_version": 1, "status": "failed",
                "failure": "runner_error_" + type(error).__name__}
        try:
            args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
            _write_private(args.output_dir / "receipt.json", safe)
        except OSError:
            pass
        return 2


def _arm_token_count(receipt: dict[str, Any]) -> tuple[int | None, bool]:
    arms = receipt.get("arms")
    if not isinstance(arms, dict) or len(arms) != 2:
        return None, False
    total = 0
    for arm in arms.values():
        if not isinstance(arm, dict):
            return None, False
        usage = arm.get("token_usage")
        status = arm.get("token_usage_status")
        if usage is None:
            usage = arm.get("codex_token_usage")
            status = arm.get("codex_token_usage_status")
        if not isinstance(usage, dict) or status != "available":
            return None, False
        inputs, outputs = usage.get("input_tokens"), usage.get("output_tokens")
        if (type(inputs) is not int or inputs < 0 or type(outputs) is not int or outputs < 0):
            return None, False
        total += inputs + outputs
    return total, True


def _quality_passed(receipt: dict[str, Any], case: str) -> bool:
    if receipt.get("status") != "completed":
        return False
    arms = receipt.get("arms")
    if not isinstance(arms, dict) or set(arms) != {"arm-a", "arm-b"}:
        return False
    if case == "pretask_strategy":
        return all(
            arm.get("independent_quality_status") == "frozen_checks_pass"
            and isinstance(arm.get("independent_validation"), dict)
            and arm["independent_validation"]
            and all(code == 0 for code in arm["independent_validation"].values())
            and isinstance(arm.get("focused_test_exits"), list)
            and bool(arm["focused_test_exits"])
            and all(isinstance(item, dict) and item.get("exit_code") == 0
                    for item in arm["focused_test_exits"])
            and arm.get("required_suite_invocation_observed") is True
            and arm.get("required_suite_exit") == 0
            and arm.get("immutable_files_preserved") is True
            for arm in arms.values() if isinstance(arm, dict)
        ) and all(isinstance(arm, dict) for arm in arms.values())
    if case == "post_change_test_order":
        return (
            receipt.get("quality_gate_status") == "passed"
            and receipt.get("quality_gate_failures") == []
            and receipt.get("task_correctness_status") == "passed"
            and all(
                isinstance(arm, dict)
                and arm.get("cli_status") == "completed"
                and arm.get("independent_final_validation_status") == "passed"
                and arm.get("immutable_files_preserved") is True
                and arm.get("changed_source_observed") is True
                and bool(arm.get("required_suite_invocation_observed"))
                and arm.get("required_suite_exit") == 0
                for arm in arms.values()
            )
        )
    if case == "ambiguous_failure_triage":
        return (
            isinstance(receipt.get("task_correctness"), dict)
            and all(receipt.get("task_correctness", {}).get(label) == "passed"
                    for label in arms)
            and all(
                isinstance(arm, dict)
                and arm.get("cli_status") == "completed"
                and arm.get("immutable_fixture_unchanged") is True
                and receipt.get("independent_validation", {}).get(label, {}).get("focused", {}).get("status") == "passed"
                and receipt.get("independent_validation", {}).get(label, {}).get("full", {}).get("status") == "passed"
                for label, arm in arms.items()
            )
        )
    return False


def _launch_pair(
    case: str, row: dict[str, Any], *, settings: dict[str, Any],
    output_dir: Path, remaining_runtime: float, remaining_tokens: int,
) -> tuple[dict[str, Any] | None, str, float]:
    timeout = settings["per_arm_timeout_seconds"]
    max_tokens = min(settings["per_arm_agent_token_budget"], remaining_tokens // 2)
    if max_tokens < 1:
        return None, "not_run_total_token_cap", 0.0
    pair_output = output_dir / row["pair"]
    if pair_output.exists() or pair_output.is_symlink():
        return None, "refused_existing_receipt_path", 0.0
    argv = [
        sys.executable, str(SCRIPT), "--_run-pair",
        "--case", case, "--model", settings["model"],
        "--codex", settings["codex_executable"],
        "--reasoning-effort", settings["reasoning_effort"],
        "--timeout", str(timeout), "--max-tokens", str(max_tokens),
        "--seed", str(row["seed"]), "--output-dir", str(pair_output),
        "--requested-route", settings["requested_routes"][case],
    ]
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        return None, "failed_to_start_pair", 0.0
    try:
        process.wait(timeout=max(0.05, remaining_runtime))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait()
        elapsed = time.monotonic() - started
        return None, "timed_out_total_runtime_cap", elapsed
    elapsed = time.monotonic() - started
    receipt_path = pair_output / "receipt.json"
    map_path = pair_output / "arm-map.json"
    receipt = None
    arm_map = None
    try:
        receipt = _load_json(receipt_path)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    try:
        arm_map = _load_json(map_path)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    if receipt is None:
        return None, "receipt_missing", elapsed
    return {"receipt": receipt, "arm_map": arm_map}, (
        "completed" if process.returncode == 0 and _quality_passed(receipt, case) else "failed"
    ), elapsed


def run_cohort(
    manifest: dict[str, Any], *, cohort: str, output_dir: Path,
    launcher: Callable[..., tuple[dict[str, Any] | None, str, float]] = _launch_pair,
) -> dict[str, Any]:
    if cohort not in ("feasibility", "main"):
        raise ValueError("unknown cohort")
    issues = _current_fingerprint(manifest)
    if issues or manifest.get("execution_status") not in ("ready", "ready_partial"):
        raise ValueError("frozen manifest validation failed")
    settings = manifest["settings"]
    if output_dir.is_symlink():
        raise ValueError("output directory must not be a symlink")
    if not output_dir.exists():
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    elif not output_dir.is_dir():
        raise ValueError("output path is not a directory")
    os.chmod(output_dir, 0o700)
    state_path = output_dir / "cohort-ledger.json"
    if state_path.exists() or state_path.is_symlink():
        state = _load_json(state_path)
    else:
        state = {
            "schema_version": 1, "manifest_sha256": _sha(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()),
            "runtime_seconds_used": 0.0, "agent_tokens_used": 0,
            "pairs": {}, "terminal_status": "in_progress",
        }
    expected_state = "in_progress" if cohort == "feasibility" else "feasibility_complete"
    if state.get("terminal_status") != expected_state:
        raise ValueError("cohort phase is terminal or out of order; reruns are not allowed")
    if cohort == "main":
        for case in CASE_ORDER:
            rows = [r for r in manifest["schedules"]["feasibility"] if r["case"] == case]
            if not rows or manifest["cases"][case]["status"] not in ("ready", "ready_local_only"):
                continue
            prior = [state["pairs"].get(row["pair"], {}) for row in rows]
            if not all(item.get("status") == "completed" for item in prior):
                # The slice is retained but stopped; unrelated slices may continue.
                state.setdefault("stopped_cases", []).append(case)
        if any(pair_id in state["pairs"] for pair_id in
               [r["pair"] for r in manifest["schedules"]["main"]]):
            raise ValueError("main cohort already contains attempted pairs")

    rows = manifest["schedules"][cohort]
    stopped_cases = set(state.get("stopped_cases", []))
    for row in rows:
        pair_id, case = row["pair"], row["case"]
        if pair_id in state["pairs"]:
            raise ValueError("scheduled pair already has a terminal record")
        if case in stopped_cases:
            status = "not_run_case_stopped"
            state["pairs"][pair_id] = {
                "case": case, "seed": row["seed"], "first_arm": row["first_arm"],
                "status": status, "receipt_path": None, "arm_map_path": None,
            }
            continue
        if manifest["cases"][case]["status"] not in ("ready", "ready_local_only"):
            state["pairs"][pair_id] = {
                "case": case, "seed": row["seed"], "first_arm": row["first_arm"],
                "status": "unavailable_case", "receipt_path": None, "arm_map_path": None,
            }
            continue
        if state["runtime_seconds_used"] >= settings["total_runtime_seconds"]:
            state["terminal_status"] = "stopped"
            state["pairs"][pair_id] = {
                "case": case, "seed": row["seed"], "first_arm": row["first_arm"],
                "status": "not_run_total_runtime_cap", "receipt_path": None, "arm_map_path": None,
            }
            for later in rows[rows.index(row) + 1:]:
                if later["pair"] not in state["pairs"]:
                    state["pairs"][later["pair"]] = {
                        "case": later["case"], "seed": later["seed"],
                        "first_arm": later["first_arm"], "status": "not_run_after_cohort_stop",
                        "receipt_path": None, "arm_map_path": None,
                    }
            break
        if state["agent_tokens_used"] >= settings["total_agent_token_budget"]:
            state["terminal_status"] = "stopped"
            state["pairs"][pair_id] = {
                "case": case, "seed": row["seed"], "first_arm": row["first_arm"],
                "status": "not_run_total_token_cap", "receipt_path": None, "arm_map_path": None,
            }
            for later in rows[rows.index(row) + 1:]:
                if later["pair"] not in state["pairs"]:
                    state["pairs"][later["pair"]] = {
                        "case": later["case"], "seed": later["seed"],
                        "first_arm": later["first_arm"], "status": "not_run_after_cohort_stop",
                        "receipt_path": None, "arm_map_path": None,
                    }
            break
        remaining_runtime = settings["total_runtime_seconds"] - state["runtime_seconds_used"]
        remaining_tokens = settings["total_agent_token_budget"] - state["agent_tokens_used"]
        result, status, elapsed = launcher(
            case, row, settings=settings, output_dir=output_dir,
            remaining_runtime=remaining_runtime, remaining_tokens=remaining_tokens,
        )
        state["runtime_seconds_used"] += elapsed
        arm_receipt = result["receipt"] if isinstance(result, dict) else None
        arm_map = result.get("arm_map") if isinstance(result, dict) else None
        token_count, token_count_known = (
            _arm_token_count(arm_receipt) if isinstance(arm_receipt, dict) else (None, False))
        if token_count_known:
            state["agent_tokens_used"] += token_count
        if not token_count_known:
            status = "token_usage_unknown_stop"
        if isinstance(arm_receipt, dict) and any(
            isinstance(arm, dict) and arm.get("failure") == "token_budget_exceeded"
            for arm in arm_receipt.get("arms", {}).values()
        ):
            status = "per_arm_token_cap_reached_stop"
        state["pairs"][pair_id] = {
            "case": case, "seed": row["seed"], "first_arm": row["first_arm"],
            "status": status,
            "receipt_path": f"{pair_id}/receipt.json" if arm_receipt is not None else None,
            "arm_map_path": f"{pair_id}/arm-map.json" if isinstance(arm_map, dict) else None,
            "runner_status": arm_receipt.get("status") if isinstance(arm_receipt, dict) else None,
            "quality_gates_passed": _quality_passed(arm_receipt, case) if isinstance(arm_receipt, dict) else False,
            "agent_tokens": token_count,
            "agent_token_usage_known": token_count_known,
            "elapsed_seconds": round(elapsed, 3),
        }
        stop = status in ("timed_out_total_runtime_cap", "not_run_total_token_cap",
                          "per_arm_token_cap_reached_stop", "token_usage_unknown_stop",
                          "receipt_missing", "failed_to_start_pair")
        if stop:
            for later in rows[rows.index(row) + 1:]:
                later_id = later["pair"]
                if later_id not in state["pairs"]:
                    state["pairs"][later_id] = {
                        "case": later["case"], "seed": later["seed"], "first_arm": later["first_arm"],
                        "status": "not_run_after_cohort_stop", "receipt_path": None,
                        "arm_map_path": None,
                    }
            state["terminal_status"] = "stopped"
            break
        if status != "completed":
            stopped_cases.add(case)
            state["stopped_cases"] = sorted(stopped_cases)
            for later in rows[rows.index(row) + 1:]:
                if later["case"] == case and later["pair"] not in state["pairs"]:
                    state["pairs"][later["pair"]] = {
                        "case": case, "seed": later["seed"],
                        "first_arm": later["first_arm"],
                        "status": "not_run_case_stopped", "receipt_path": None,
                        "arm_map_path": None,
                    }
    else:
        state["terminal_status"] = "feasibility_complete" if cohort == "feasibility" else "completed"
    state["last_cohort"] = cohort
    state["last_cohort_status"] = "completed" if state["terminal_status"] != "stopped" else "stopped"
    _write_state(state_path, state)
    return state


def _write_state(path: Path, state: dict[str, Any]) -> None:
    # Atomic private ledger update; safe for the two ordered phase invocations.
    temp = path.with_name(path.name + ".tmp")
    data = (json.dumps(state, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
    os.replace(temp, path)
    os.chmod(path, 0o600)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True,
                        help="write a frozen manifest by default; read one with --run")
    parser.add_argument("--run", action="store_true",
                        help="execute one fixed cohort; omitted means offline manifest creation")
    parser.add_argument("--cohort", choices=("feasibility", "main"), default="feasibility")
    parser.add_argument("--output-dir", type=Path,
                        help="private receipt root used only with --run")
    parser.add_argument("--model")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--per-arm-timeout-seconds", type=int)
    parser.add_argument("--per-arm-token-budget", type=int)
    parser.add_argument("--total-runtime-seconds", type=int)
    parser.add_argument("--total-agent-token-budget", type=int)
    parser.add_argument("--codex-identity")
    parser.add_argument("--installed-jev-version")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    child_result = _child_main(argv)
    if child_result is not None:
        return child_result
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if not args.run:
            if args.model is None or args.reasoning_effort is None:
                parser.error("offline freeze requires --model and --reasoning-effort")
            required = (args.per_arm_timeout_seconds, args.per_arm_token_budget,
                        args.total_runtime_seconds, args.total_agent_token_budget)
            if any(value is None for value in required):
                parser.error("offline freeze requires all finite per-arm and total caps")
            manifest = build_manifest(
                model=args.model, reasoning_effort=args.reasoning_effort,
                codex_executable=args.codex,
                per_arm_timeout_seconds=args.per_arm_timeout_seconds,
                per_arm_token_budget=args.per_arm_token_budget,
                total_runtime_seconds=args.total_runtime_seconds,
                total_agent_token_budget=args.total_agent_token_budget,
                codex_identity=args.codex_identity,
                installed_jev_version=args.installed_jev_version,
            )
            _write_private(args.manifest, manifest)
            print(json.dumps({
                "execution_status": manifest["execution_status"],
                "launch_authorized": False,
                "case_statuses": {key: value["status"] for key, value in manifest["cases"].items()},
                "blocker_count": len(manifest["blockers"]),
            }, sort_keys=True))
            return 0 if manifest["execution_status"] in ("ready", "ready_partial") else 2
        if args.output_dir is None:
            parser.error("--run requires --output-dir")
        manifest = _load_json(args.manifest)
        issues = _current_fingerprint(manifest)
        if issues:
            print(json.dumps({"execution_status": "not_ready", "issues": issues}, sort_keys=True))
            return 2
        state = run_cohort(manifest, cohort=args.cohort, output_dir=args.output_dir)
        print(json.dumps({
            "terminal_status": state["terminal_status"],
            "last_cohort_status": state["last_cohort_status"],
            "pair_count": len(state["pairs"]),
            "agent_tokens_used": state["agent_tokens_used"],
        }, sort_keys=True))
        return 0 if state["last_cohort_status"] == "completed" else 1
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
        print(json.dumps({"execution_status": "failed", "reason": "bounded_preflight_or_runner_failure"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
