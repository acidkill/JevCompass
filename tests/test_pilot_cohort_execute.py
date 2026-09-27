from __future__ import annotations

import sys
import unittest
import tempfile
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
    assert triage["remote_route_verified"] is True
    assert triage["requested_route"] == "supervisor_loopback_enum_bridge_agents_keyless_equal_network"
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


def test_timeout_bridge_route_is_forwarded_without_agent_key():
    import pilot_ambiguous_timeout_pair as triage

    captured = {}

    def fake_run_pair(**kwargs):
        captured.update(kwargs)
        return {"status": "failed", "arms": {}}

    with mock.patch.object(triage, "run_pair", fake_run_pair):
        execute._run_case_child(
            "ambiguous_failure_triage", codex="codex", model="gpt-6-luna",
            reasoning_effort="low", timeout=60, max_tokens=100, seed=7,
            output_dir=Path("/unused-synthetic-output"),
            requested_route="supervisor_loopback_enum_bridge_agents_keyless_equal_network",
        )
    assert captured["allow_supervisor_triage"] is True


def test_timeout_quality_gate_rejects_missing_or_failed_required_checks():
    arm = {
        "cli_status": "completed", "first_tool_was_focused": True,
        "initial_focused_exit": 1, "initial_timeout_error_observed": True,
        "focused_rerun_exit": 0, "full_suite_exit": 0,
        "event_sequence_valid": True, "source_artifact_changed": True,
        "immutable_fixture_unchanged": True,
    }
    independent = {
        label: {"focused": {"status": "passed"}, "full": {"status": "passed"}}
        for label in ("arm-a", "arm-b")
    }
    receipt = {
        "status": "completed", "task_correctness": {"arm-a": "passed", "arm-b": "passed"},
        "arms": {"arm-a": arm.copy(), "arm-b": arm.copy()},
        "independent_validation": independent,
    }
    assert execute._quality_passed(receipt, "ambiguous_failure_triage")
    receipt["independent_validation"]["arm-b"]["full"]["status"] = "failed"
    assert not execute._quality_passed(receipt, "ambiguous_failure_triage")


def _synthetic_cohort_manifest():
    rows = [
        ("P-F1", "pretask_strategy"), ("P-F2", "pretask_strategy"),
        ("T-F1", "post_change_test_order"), ("T-F2", "post_change_test_order"),
        ("A-F1", "ambiguous_failure_triage"), ("A-F2", "ambiguous_failure_triage"),
    ]
    return {
        "schema_version": 1, "execution_status": "ready_partial",
        "settings": {
            "total_runtime_seconds": 1000, "total_agent_token_budget": 10000,
            "per_arm_timeout_seconds": 60, "per_arm_agent_token_budget": 500,
        },
        "cases": {case: {"status": "ready"} for case in execute.CASE_ORDER},
        "schedules": {
            "feasibility": [
                {"pair": pair, "case": case, "seed": index + 1,
                 "first_arm": "baseline"}
                for index, (pair, case) in enumerate(rows)
            ],
            "main": [],
        },
    }


def _synthetic_pair_result(status):
    return {
        "receipt": {
            "status": status,
            "arms": {
                label: {"token_usage_status": "available",
                       "token_usage": {"input_tokens": 4, "output_tokens": 2}}
                for label in ("arm-a", "arm-b")
            },
        },
        "arm_map": {"arm-a": "baseline", "arm-b": "treatment"},
    }


def test_prefilled_stopped_pair_is_skipped_and_other_case_continues():
    manifest = _synthetic_cohort_manifest()
    calls = []

    def launcher(case, row, **kwargs):
        calls.append(row["pair"])
        status = "failed" if row["pair"] == "T-F1" else "completed"
        return _synthetic_pair_result(status), status, 0.1

    with mock.patch.object(execute, "_current_fingerprint", return_value=[]):
        with tempfile.TemporaryDirectory() as directory:
            state = execute.run_cohort(
                manifest, cohort="feasibility", output_dir=Path(directory) / "out",
                launcher=launcher,
            )
            ledger = execute._load_json(Path(directory) / "out" / "cohort-ledger.json")

    assert calls == ["P-F1", "P-F2", "T-F1", "A-F1", "A-F2"]
    assert state["pairs"]["T-F2"]["status"] == "not_run_case_stopped"
    assert ledger["pairs"]["P-F1"]["status"] == "completed"
    assert ledger["pairs"]["T-F2"]["status"] == "not_run_case_stopped"
    assert state["pairs"]["A-F1"]["status"] == "completed"


def test_launcher_exception_checkpoints_started_pair_and_prior_records():
    manifest = _synthetic_cohort_manifest()
    calls = []

    def launcher(case, row, **kwargs):
        calls.append(row["pair"])
        if row["pair"] == "P-F2":
            raise OSError("synthetic failure")
        return _synthetic_pair_result("completed"), "completed", 0.1

    with mock.patch.object(execute, "_current_fingerprint", return_value=[]):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            state = execute.run_cohort(
                manifest, cohort="feasibility", output_dir=output, launcher=launcher,
            )
            ledger = execute._load_json(output / "cohort-ledger.json")

    assert calls == ["P-F1", "P-F2"]
    assert state["terminal_status"] == "stopped"
    assert ledger["pairs"]["P-F1"]["status"] == "completed"
    assert ledger["pairs"]["P-F2"]["status"] == "runner_exception_stop"
    assert ledger["pairs"]["P-F2"]["receipt_path"] is None
    assert ledger["pairs"]["T-F1"]["status"] == "not_run_after_cohort_stop"


def load_tests(loader, tests, pattern):
    """Include the function cases in the repository's unittest CI suite."""
    return unittest.TestSuite(
        unittest.FunctionTestCase(case)
        for name, case in sorted(globals().items())
        if name.startswith("test_") and callable(case)
    )
