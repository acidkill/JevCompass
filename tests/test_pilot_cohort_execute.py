from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pilot_cohort_execute as execute
import pilot_cohort_manifest as inventory


def test_fixed_schedule_is_balanced_and_verified():
    manifest = execute.build_manifest(
        root=ROOT, model="gpt-6-luna", reasoning_effort="low",
        per_arm_timeout_seconds=120, per_arm_token_budget=1000,
        total_runtime_seconds=1800, total_agent_token_budget=20000,
        codex_identity="codex-test", installed_jev_version="1.0-test",
        openrouter_key_available=True,
        probe=lambda case: {
            "status": "available", "prompt_component_sha256": {},
            "validator_source_sha256": {}, "runner_installed_version": "informational",
            "requested_route": "route", "max_tokens_applied": True,
        },
    )
    assert manifest["schedules"]["feasibility"] == inventory._schedule(inventory.FEASIBILITY)
    assert manifest["schedules"]["main"] == inventory._schedule(inventory.MAIN)
    assert len(manifest["schedules"]["feasibility"]) == 6
    assert len(manifest["schedules"]["main"]) == 20
    assert all(row["schedule_verified"] for rows in manifest["schedules"].values() for row in rows)
    assert manifest["settings"]["requested_routes"]["pretask_strategy"] == (
        "supervisor_remote_selector_agents_keyless")
    assert manifest["settings"]["requested_routes"]["post_change_test_order"] == (
        "remote_agent_cli_equal_key_environment")
    assert manifest["cases"]["ambiguous_failure_triage"]["status"] == "unavailable"


def test_all_three_isolated_runner_probes_are_safe_and_route_labeled():
    pretask = execute._run_probe("pretask_strategy")
    post_change = execute._run_probe("post_change_test_order")
    triage = execute._run_probe("ambiguous_failure_triage")
    assert pretask["status"] == "available"
    assert pretask["requested_route"] == "supervisor_remote_selector_agents_keyless"
    assert post_change["status"] == "available"
    assert post_change["requested_route"] == "remote_agent_cli_equal_key_environment"
    assert triage["status"] == "available"
    assert triage["remote_route_verified"] is False
    assert triage["local_route_verified"] is True


def test_missing_supervisor_key_disables_remote_pretask_case():
    manifest = execute.build_manifest(
        root=ROOT, model="gpt-6-luna", reasoning_effort="low",
        per_arm_timeout_seconds=120, per_arm_token_budget=1000,
        total_runtime_seconds=1800, total_agent_token_budget=20000,
        codex_identity="codex-test", installed_jev_version="1.0-test",
        openrouter_key_available=False,
        probe=lambda case: {
            "status": "available", "prompt_component_sha256": {},
            "validator_source_sha256": {}, "runner_installed_version": "informational",
            "requested_route": "route", "max_tokens_applied": True,
        },
    )
    case = manifest["cases"]["pretask_strategy"]
    assert case["status"] == "unavailable"
    assert "requested_remote_route_key_unavailable" in case["reasons"]
    assert manifest["settings"]["openrouter_key_available"] is False


def test_completed_status_with_failed_full_validation_is_not_quality_pass():
    receipt = {
        "status": "completed",
        "quality_gate_status": "passed",
        "task_correctness_status": "passed",
        "arms": {
            label: {
                "cli_status": "completed",
                "independent_final_validation_status": "failed",
                "immutable_files_preserved": True,
                "required_suite_invocation_observed": True,
                "required_suite_exit": 1,
            }
            for label in ("arm-a", "arm-b")
        },
    }
    assert not execute._quality_passed(receipt, "post_change_test_order")


def test_quality_gate_requires_both_pretask_frozen_validation_receipts():
    arm = {
        "independent_quality_status": "frozen_checks_pass",
        "independent_validation": {"focused": 0, "full": 0},
        "focused_test_exits": [{"exit_code": 0}],
        "required_suite_invocation_observed": True,
        "required_suite_exit": 0,
        "immutable_files_preserved": True,
    }
    receipt = {"status": "completed", "arms": {"arm-a": arm, "arm-b": arm}}
    assert execute._quality_passed(receipt, "pretask_strategy")
    receipt["arms"]["arm-b"] = {**arm, "independent_validation": {"focused": 0, "full": 1}}
    assert not execute._quality_passed(receipt, "pretask_strategy")


def test_postchange_remote_route_is_frozen_and_forwarded_to_both_arm_runner():
    import pilot_target_coverage_test_order_pair as target

    captured = {}

    def fake_run_pair(**kwargs):
        captured.update(kwargs)
        return {"status": "failed"}

    with mock.patch.object(target, "run_pair", fake_run_pair):
        execute._run_case_child(
            "post_change_test_order", codex="codex", model="gpt-6-luna",
            reasoning_effort="low", timeout=60, max_tokens=100, seed=7,
            output_dir=Path("/unused-synthetic-output"),
            requested_route="remote_agent_cli_equal_key_environment",
        )
    assert captured["allow_openrouter_key"] is True


def test_wrong_requested_route_fails_closed():
    try:
        execute._run_case_child(
            "post_change_test_order", codex="codex", model="gpt-6-luna",
            reasoning_effort="low", timeout=60, max_tokens=100, seed=7,
            output_dir=Path("/unused-synthetic-output"), requested_route="keyless_local_codex_cli",
        )
    except RuntimeError as error:
        assert str(error) == "requested_route_mismatch"
    else:
        raise AssertionError("unfrozen route was accepted")


def load_tests(loader, tests, pattern):
    """Include the function cases in the repository's unittest CI suite."""
    return unittest.TestSuite(
        unittest.FunctionTestCase(case)
        for name, case in sorted(globals().items())
        if name.startswith("test_") and callable(case)
    )
