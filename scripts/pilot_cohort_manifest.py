#!/usr/bin/env python3
"""Build/validate an offline, fail-closed manifest for BENCHMARK_PROTOCOL.md.

This tool never launches an agent or reads credentials. A not-ready manifest is
useful inventory, not authorization to execute the cohort.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = "BENCHMARK_PROTOCOL.md"
FEASIBILITY = [
    ("P-F1", "pretask_strategy", 28101, "baseline"),
    ("P-F2", "pretask_strategy", 28104, "treatment"),
    ("T-F1", "post_change_test_order", 28102, "baseline"),
    ("T-F2", "post_change_test_order", 28106, "treatment"),
    ("A-F1", "ambiguous_failure_triage", 28103, "baseline"),
    ("A-F2", "ambiguous_failure_triage", 28107, "treatment"),
]
MAIN = [
    ("P1", "pretask_strategy", 28001, "baseline"),
    ("T1", "post_change_test_order", 28006, "baseline"),
    ("A1", "ambiguous_failure_triage", 28018, "baseline"),
    ("P2", "pretask_strategy", 28004, "treatment"),
    ("T2", "post_change_test_order", 28011, "treatment"),
    ("A2", "ambiguous_failure_triage", 28015, "treatment"),
    ("P3", "pretask_strategy", 28002, "baseline"),
    ("T3", "post_change_test_order", 28008, "baseline"),
    ("A3", "ambiguous_failure_triage", 28019, "baseline"),
    ("P4", "pretask_strategy", 28007, "treatment"),
    ("T4", "post_change_test_order", 28012, "treatment"),
    ("A4", "ambiguous_failure_triage", 28016, "treatment"),
    ("P5", "pretask_strategy", 28003, "baseline"),
    ("T5", "post_change_test_order", 28009, "baseline"),
    ("A5", "ambiguous_failure_triage", 28020, "baseline"),
    ("P6", "pretask_strategy", 28010, "treatment"),
    ("T6", "post_change_test_order", 28013, "treatment"),
    ("A6", "ambiguous_failure_triage", 28017, "treatment"),
    ("P7", "pretask_strategy", 28005, "baseline"),
    ("T7", "post_change_test_order", 28014, "treatment"),
]
PINNED = {
    "post_change_test_order": {
        "tests/fixtures/cross_layer_test_order/README.md": "5f7e9eedaffa6a076b072d413456a03903bfb35448d8406c1b19c0ad0819e6e3",
        "tests/fixtures/cross_layer_test_order/CONTRACT.md": "a58b3723cb50f32b68de748c42b0b52b51f05eb38aa55dc044b895a7d2aa18d4",
        "tests/fixtures/cross_layer_test_order/TESTING.md": "e085b58c31ea651eb9df3317fa7c8683ac31742584a2b2890a877e11d5add5e7",
        "tests/fixtures/cross_layer_test_order/test-options.json": "303f55f544e96977d87164c041a780251080e7664dc0eb0605e17333f37f16f0",
        "tests/fixtures/cross_layer_test_order/checkout/service.py": "6153c27ac5d0aa9a7855530059d597e7f710d41488b39b61e257705fd6f5fb3f",
        "tests/fixtures/cross_layer_test_order/tests/test_unit_checkout.py": "a1afeabb09793f251d8b4fa0116a03548981c558a120e2ba9775e1c149915590",
        "tests/fixtures/cross_layer_test_order/tests/test_integration_checkout.py": "e21b9fb91ca939899c6009520f70cc27b1959ebd428d00957bb8e402bf341977",
    },
    "ambiguous_failure_triage": {
        "tests/fixtures/condition_timeout_triage/CONTRACT.md": "03b8fbd54422da470ca44ad70461ca6982cdd2f7158f3326f7fd68aee785104e",
        "tests/fixtures/condition_timeout_triage/README.md": "7edd22c3ee95792d21145883256e09465b6b5eedcc41c612b67388c7206aac71",
        "tests/fixtures/condition_timeout_triage/inbox.py": "92eebfd7ae4a6d32c2594764bc0c67770562fc2a970af274897439e04f945d08",
        "tests/fixtures/condition_timeout_triage/tests/test_inbox.py": "db8b8c6c45d2c1cd34f3d9996abd248ed4900eb78efdecb3c4399263ecce13f4",
    },
}
CASE_FILES = {
    "pretask_strategy": [
        "tests/fixtures/coding_test_order/CONTRACT.md",
        "tests/fixtures/coding_test_order/TESTING.md",
        "tests/fixtures/coding_test_order/parcelquote/__init__.py",
        "tests/fixtures/coding_test_order/parcelquote/__main__.py",
        "tests/fixtures/coding_test_order/parcelquote/quote.py",
        "tests/fixtures/coding_test_order/test-options.json",
        "tests/fixtures/coding_test_order/tests/test_contract_cli.py",
        "tests/fixtures/coding_test_order/tests/test_unit_quote.py",
    ],
    "post_change_test_order": [
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
    ],
    "ambiguous_failure_triage": list(PINNED["ambiguous_failure_triage"]),
}
RUNNERS = [
    "scripts/pilot_cli_core.py",
    "scripts/pilot_receipts.py",
    "scripts/pilot_strategy_pair.py",
    "scripts/pilot_pretask_strategy_pair.py",
    "scripts/pilot_test_order_pair.py",
    "scripts/pilot_cross_layer_test_order_pair.py",
    "scripts/pilot_target_coverage_test_order_pair.py",
    "scripts/pilot_condition_timeout_pair.py",
    "tests/test_pilot_dependency_strategy_pair.py",
    "tests/test_pilot_test_order_pair.py",
    "tests/test_pilot_cross_layer_test_order_pair.py",
    "tests/test_pilot_target_coverage_test_order_pair.py",
    "tests/test_pilot_condition_timeout_pair.py",
    "tests/test_cross_layer_test_order_fixture.py",
    "tests/test_condition_timeout_triage_fixture.py",
    "tests/evaluation/cross_layer_decision_protocol.md",
]
PRETASK_SOURCE_TREE_SHA256 = "5dc21dd5ed1f38d92dddea17344ce9a865849cb384668f17e4e842065ffda057"
PRETASK_PRIOR_RUNNER_TREE_SHA256 = "db47b815d401bf3e05809215c993532254e80d2f18013c72c660b6a07b7cff13"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _fixture_tree(root: Path, case: str) -> tuple[str | None, list[str]]:
    prefix = {
        "pretask_strategy": "tests/fixtures/coding_test_order",
        "post_change_test_order": "tests/fixtures/cross_layer_test_order",
        "ambiguous_failure_triage": "tests/fixtures/condition_timeout_triage",
    }[case]
    base = root / prefix
    if not base.is_dir() or base.is_symlink():
        return None, []
    found = []
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            return None, []
        if (path.is_file() and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo"}):
            found.append(path.relative_to(root).as_posix())
    digest = hashlib.sha256()
    for rel in found:
        raw = (root / rel).read_bytes()
        relative = rel[len(prefix) + 1 :].encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest(), found


def _schedule(rows: list[tuple[str, str, int, str]]) -> list[dict[str, Any]]:
    out = []
    for pair, case, seed, expected in rows:
        arms = ["baseline", "treatment"]
        random.Random(seed).shuffle(arms)
        observed = arms[0]
        out.append({"pair": pair, "case": case, "seed": seed,
                    "first_arm": observed, "schedule_verified": observed == expected})
    return out


def build_manifest(
    root: Path = ROOT,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    per_arm_timeout_seconds: int | None = None,
    total_runtime_seconds: int | None = None,
    per_arm_token_budget: int | None = None,
    total_token_budget: int | None = None,
    execution_path: str | None = None,
    expected_identity: str | None = None,
    observed_identity: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    blockers: list[str] = []
    for name, value in (
        ("model", model), ("reasoning_effort", reasoning_effort),
        ("per_arm_timeout_seconds", per_arm_timeout_seconds),
        ("total_runtime_seconds", total_runtime_seconds),
        ("per_arm_token_budget", per_arm_token_budget),
        ("total_token_budget", total_token_budget),
        ("execution_path", execution_path), ("expected_identity", expected_identity),
        ("observed_identity", observed_identity),
    ):
        if value is None:
            blockers.append(f"missing:{name}")
    if per_arm_timeout_seconds is not None and not 1 <= per_arm_timeout_seconds <= 180:
        blockers.append("invalid:per_arm_timeout_seconds")
    if (total_runtime_seconds is not None and per_arm_timeout_seconds is not None
            and total_runtime_seconds < per_arm_timeout_seconds):
        blockers.append("insufficient:total_runtime_seconds")
    if per_arm_token_budget is not None and per_arm_token_budget < 1:
        blockers.append("invalid:per_arm_token_budget")
    if (total_token_budget is not None and per_arm_token_budget is not None
            and total_token_budget < per_arm_token_budget):
        blockers.append("insufficient:total_token_budget")
    blockers.append("token_budget_monitor_not_wired_to_cohort_runners")
    if execution_path not in (None, "source_checkout_cli", "installed_cli"):
        blockers.append("invalid:execution_path")
    if expected_identity and expected_identity != observed_identity:
        blockers.append("identity_mismatch")

    protocol_path = root / PROTOCOL
    if not protocol_path.is_file():
        blockers.append("missing:protocol")
        protocol_hash = None
    else:
        protocol_hash = _sha(protocol_path)
    revision = None
    try:
        revision = _git(root, "rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError):
        blockers.append("missing:git_revision")

    artifacts: dict[str, Any] = {}
    paths = sorted(set(RUNNERS + [PROTOCOL] + [p for files in CASE_FILES.values() for p in files]))
    for rel in paths:
        path = root / rel
        if path.is_symlink() or not path.is_file():
            artifacts[rel] = {"status": "unavailable", "sha256": None}
            blockers.append(f"artifact_unavailable:{rel}")
            continue
        try:
            dirty = bool(_git(root, "status", "--porcelain", "--", rel))
        except (OSError, subprocess.CalledProcessError):
            dirty = True
        artifacts[rel] = {"status": "dirty" if dirty else "pinned", "sha256": _sha(path)}
        if dirty:
            blockers.append(f"artifact_dirty:{rel}")

    case_inventory = {}
    for case, files in CASE_FILES.items():
        tree_hash, found = _fixture_tree(root, case)
        expected_files = sorted(files)
        status = "ready"
        reasons = []
        if tree_hash is None or sorted(found) != expected_files:
            status = "unavailable"
            reasons.append("fixture_file_set_mismatch")
        if case == "pretask_strategy":
            if tree_hash != PRETASK_SOURCE_TREE_SHA256:
                status = "unavailable"
                reasons.append("reviewed_source_only_fixture_hash_mismatch")
        else:
            for rel, digest in PINNED[case].items():
                artifact = artifacts.get(rel, {})
                if artifact.get("sha256") != digest:
                    status = "unavailable"
                    reasons.append(f"reviewed_hash_mismatch:{rel}")
        if case == "ambiguous_failure_triage":
            status = "unavailable"
            reasons.append("remote_ambiguous_stratum_unavailable_runner_resolves_locally_and_strips_key")
        if status != "ready":
            blockers.append(f"case_unavailable:{case}")
        case_inventory[case] = {
            "status": status,
            "scope": ("repeated_task_descriptive_only" if case == "pretask_strategy"
                      else "post_change_coverage" if case == "post_change_test_order"
                      else "remote_ambiguous_unavailable_local_only"),
            "fixture_tree_sha256": tree_hash,
            "fixture_hash_scope": "source_files_excluding_generated_bytecode",
            "legacy_full_runner_tree_sha256": (
                PRETASK_PRIOR_RUNNER_TREE_SHA256 if case == "pretask_strategy" else None),
            "fixture_files": found,
            "reasons": reasons,
        }

    unsupported = root / "tests" / "fixtures" / "strategy_dependency_change"
    excluded_controls = [{
        "id": "strategy_dependency_change_local_control",
        "status": "unavailable",
        "reason": "fully_specified_local_resolution_control_not_genuine_remote_ambiguity",
        "present": unsupported.is_dir(),
    }]
    focused_full = {
        "pretask_strategy": {
            "focused": ["python -m unittest discover -s tests -p 'test_unit*.py' -v",
                        "python -m unittest discover -s tests -p 'test_contract*.py' -v"],
            "full": "python -m unittest discover -s tests -v",
        },
        "post_change_test_order": {
            "focused": ["python -m unittest discover -s tests -p 'test_unit*.py' -v",
                        "python -m unittest discover -s tests -p 'test_integration*.py' -v"],
            "full": "python -m unittest discover -s tests -v",
        },
        "ambiguous_failure_triage": {
            "focused": ["python -m unittest discover -s tests -p 'test_inbox.py' -v"],
            "full": "python -m unittest discover -s tests -v",
        },
    }
    return {
        "schema_version": 1,
        "status": "ready" if not blockers else "not_ready",
        "protocol": {"path": PROTOCOL, "sha256": protocol_hash},
        "repository": {"revision": revision},
        "settings": {
            "model": model,
            "reasoning_effort": reasoning_effort,
            "execution_path": execution_path,
            "expected_identity": expected_identity,
            "observed_identity": observed_identity,
            "per_arm_timeout_seconds": per_arm_timeout_seconds,
            "total_runtime_seconds": total_runtime_seconds,
            "per_arm_agent_token_budget": per_arm_token_budget,
            "total_agent_token_budget": total_token_budget,
            "token_limit_control": "unavailable_runner_callers_do_not_pass_budget_to_event_collector",
            "billing": "unknown_unless_provider_reports",
        },
        "schedules": {"feasibility": _schedule(FEASIBILITY), "main": _schedule(MAIN)},
        "cases": case_inventory,
        "excluded_controls": excluded_controls,
        "artifacts": artifacts,
        "commands": focused_full,
        "blockers": sorted(set(blockers)),
        "launch_authorized": False,
    }


def validate_manifest(manifest: dict[str, Any], root: Path = ROOT) -> list[str]:
    issues = []
    if not isinstance(manifest, dict):
        return ["invalid_manifest_type"]
    if manifest.get("schema_version") != 1 or manifest.get("launch_authorized") is not False:
        issues.append("invalid_manifest_envelope")
    protocol_path = root / PROTOCOL
    if not protocol_path.is_file() or protocol_path.is_symlink():
        return issues + ["protocol_unavailable"]
    protocol = manifest.get("protocol")
    if not isinstance(protocol, dict) or protocol.get("path") != PROTOCOL or protocol.get("sha256") != _sha(protocol_path):
        issues.append("protocol_hash_changed")
    allowed = set(RUNNERS + [PROTOCOL] + [p for files in CASE_FILES.values() for p in files])
    artifacts = manifest.get("artifacts")
    allowed = set(RUNNERS + [PROTOCOL] + [p for files in CASE_FILES.values() for p in files])
    if not isinstance(artifacts, dict):
        issues.append("artifact_inventory_mismatch")
        artifacts = {}
    elif set(artifacts) != allowed:
        issues.append("artifact_inventory_mismatch")
    for rel, recorded in artifacts.items():
        if not isinstance(rel, str) or Path(rel).is_absolute() or ".." in Path(rel).parts or not isinstance(recorded, dict):
            issues.append("invalid_artifact_entry")
            continue
        if rel not in allowed:
            issues.append("invalid_artifact_entry")
            continue
        path = root / rel
        if (not path.is_file() or path.is_symlink()
                or _sha(path) != recorded.get("sha256")):
            issues.append(f"artifact_changed:{rel}")
        try:
            dirty = bool(_git(root, "status", "--porcelain", "--", rel))
        except (OSError, subprocess.CalledProcessError):
            dirty = True
        if dirty:
            issues.append(f"artifact_dirty:{rel}")
    repository = manifest.get("repository")
    try:
        current_revision = _git(root, "rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError):
        current_revision = None
    if (not isinstance(repository, dict) or repository.get("revision") is None
            or current_revision is None or repository.get("revision") != current_revision):
        issues.append("repository_revision_changed")
    expected_schedules = {"feasibility": _schedule(FEASIBILITY), "main": _schedule(MAIN)}
    if manifest.get("schedules") != expected_schedules:
        issues.append("schedule_mismatch")
    settings = manifest.get("settings")
    if not isinstance(settings, dict):
        issues.append("settings_missing")
    else:
        for field in ("model", "reasoning_effort", "execution_path", "expected_identity",
                      "observed_identity", "per_arm_timeout_seconds", "total_runtime_seconds",
                      "per_arm_agent_token_budget", "total_agent_token_budget"):
            if settings.get(field) is None:
                issues.append(f"setting_missing:{field}")
        if settings.get("expected_identity") != settings.get("observed_identity"):
            issues.append("identity_mismatch")
        if not isinstance(settings.get("model"), str) or not settings["model"].strip():
            issues.append("invalid:model")
        if not isinstance(settings.get("reasoning_effort"), str) or not settings["reasoning_effort"].strip():
            issues.append("invalid:reasoning_effort")
        if settings.get("execution_path") not in ("source_checkout_cli", "installed_cli"):
            issues.append("invalid:execution_path")
        for field in ("expected_identity", "observed_identity"):
            value = settings.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:+/@-]{1,160}", value):
                issues.append(f"invalid:{field}")
        numeric_fields = ("per_arm_timeout_seconds", "total_runtime_seconds",
                          "per_arm_agent_token_budget", "total_agent_token_budget")
        for field in numeric_fields:
            if type(settings.get(field)) is not int or settings[field] <= 0:
                issues.append(f"invalid:{field}")
        if (type(settings.get("per_arm_timeout_seconds")) is int
                and settings["per_arm_timeout_seconds"] > 180):
            issues.append("invalid:per_arm_timeout_seconds")
        if (type(settings.get("per_arm_timeout_seconds")) is int
                and type(settings.get("total_runtime_seconds")) is int
                and settings["total_runtime_seconds"] < settings["per_arm_timeout_seconds"]):
            issues.append("insufficient:total_runtime_seconds")
        if (type(settings.get("per_arm_agent_token_budget")) is int
                and type(settings.get("total_agent_token_budget")) is int
                and settings["total_agent_token_budget"] < settings["per_arm_agent_token_budget"]):
            issues.append("insufficient:total_token_budget")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--per-arm-timeout-seconds", type=int)
    parser.add_argument("--total-runtime-seconds", type=int)
    parser.add_argument("--per-arm-token-budget", type=int)
    parser.add_argument("--total-token-budget", type=int)
    parser.add_argument("--execution-path", choices=("source_checkout_cli", "installed_cli"))
    parser.add_argument("--expected-identity")
    parser.add_argument("--observed-identity")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.validate:
            manifest = json.loads(args.output.read_text(encoding="utf-8"))
            issues = validate_manifest(manifest)
            print(json.dumps({"status": "valid" if not issues else "invalid",
                              "issues": issues}, sort_keys=True))
            return 0 if not issues else 2
        manifest = build_manifest(
            model=args.model, reasoning_effort=args.reasoning_effort,
            per_arm_timeout_seconds=args.per_arm_timeout_seconds,
            total_runtime_seconds=args.total_runtime_seconds,
            per_arm_token_budget=args.per_arm_token_budget,
            total_token_budget=args.total_token_budget,
            execution_path=args.execution_path, expected_identity=args.expected_identity,
            observed_identity=args.observed_identity,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        print(json.dumps({"status": manifest["status"],
                          "blockers": manifest["blockers"],
                          "launch_authorized": False}, sort_keys=True))
        return 0 if manifest["status"] == "ready" else 2
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": type(error).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
