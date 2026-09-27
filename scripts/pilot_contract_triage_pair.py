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
from dataclasses import dataclass
import fnmatch

import pilot_cli_core as core
import pilot_test_order_pair as common
from pilot_receipts import (
    build_agent_measurement_receipt, parse_codex_json_events, parse_choice_receipt,
)
import pilot_triage_pair as triage_common
import pilot_profile_triage_bridge as profile_bridge


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
PROFILE_TRIAGE_RESULT_ACK_LINE = "JevCompass triage result received."
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


@dataclass(frozen=True)
class CaseProfile:
    case_id: str
    fixture_source: Path
    task_prompt: str
    source_file: str
    focused_test_file: str
    focused_command: tuple[str, ...]
    evidence_files: dict[str, str]
    evidence_markers: dict[str, tuple[str, ...]]
    failure_markers: tuple[str, ...]
    triage_kinds: tuple[str, ...]
    triage_hypotheses: tuple[str, ...]
    triage_accepted_ids: tuple[str, ...]
    triage_accepted_statuses: tuple[str, ...]
    triage_observations: dict[str, tuple[str, ...]]
    rank_hypotheses: bool
    outcome_mode: str
    oracle_script: Path
    oracle_sha256: str


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate case-profile key")
        result[key] = value
    return result


def _case_path(root: Path, value: Any, *, required: bool = True) -> str:
    if (not isinstance(value, str) or not value or len(value) > 240
            or value.startswith(("/", "\\\\")) or "\\\\" in value):
        raise ValueError("invalid case-profile path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid case-profile path")
    candidate = root
    for part in parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("case-profile paths cannot traverse symlinks")
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError("case-profile path escapes fixture") from exc
    if not candidate.is_file():
        raise ValueError("case-profile file is unavailable")
    if required and candidate.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("case-profile file is too large")
    return "/".join(parts)


def _case_command(value: Any) -> tuple[str, ...]:
    if (not isinstance(value, list) or not value
            or len(value) > 32
            or any(not isinstance(part, str) or not part or len(part) > 200 for part in value)):
        raise ValueError("invalid case-profile command")
    command = tuple(value)
    if (len(command) < 4 or command[0] not in {"python", "python3"}
            or command[1:3] != ("-m", "unittest")):
        raise ValueError("case-profile commands must use Python unittest")
    if any(part.startswith("-") and part not in {
        "discover", "-s", "-p", "-t", "-v", "-q", "--locals", "--buffer",
    } for part in command[3:]):
        raise ValueError("unsupported case-profile unittest option")
    return command


def load_case_profile(path: Path) -> CaseProfile:
    """Load a bounded, explicit profile without accepting shell commands or free-form enums."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
        raise ValueError("case profile must be a regular file no larger than 64 KiB")
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid case-profile JSON") from exc
    keys = {
        "schema_version", "case_id", "fixture_source", "task_prompt_file",
        "source_file", "focused_test_file", "focused_command", "evidence_files",
        "evidence_markers", "failure_markers", "triage", "outcome_mode",
        "oracle_script", "oracle_sha256",
    }
    if (not isinstance(raw, dict) or set(raw) != keys
            or type(raw["schema_version"]) is not int or raw["schema_version"] != 1):
        raise ValueError("unsupported case-profile schema")
    case_id = raw["case_id"]
    if not isinstance(case_id, str) or not SAFE_ID.fullmatch(case_id):
        raise ValueError("invalid case-profile id")
    fixture_rel = raw["fixture_source"]
    if (not isinstance(fixture_rel, str) or not fixture_rel
            or Path(fixture_rel).is_absolute() or ".." in Path(fixture_rel).parts
            or "\\\\" in fixture_rel):
        raise ValueError("invalid case-profile fixture")
    fixture_lexical = ROOT / fixture_rel
    if any((ROOT / Path(*Path(fixture_rel).parts[:index])).is_symlink()
           for index in range(1, len(Path(fixture_rel).parts) + 1)):
        raise ValueError("case-profile fixture cannot traverse symlinks")
    fixture = fixture_lexical.resolve()
    if ROOT.resolve() not in fixture.parents or not fixture.is_dir():
        raise ValueError("case-profile fixture must be a repository directory")
    prompt_file = _case_path(fixture, raw["task_prompt_file"])
    prompt = (fixture / prompt_file).read_text(encoding="utf-8")
    if not prompt.strip() or len(prompt.encode("utf-8")) > 16 * 1024:
        raise ValueError("invalid case-profile task prompt")
    source_file = _case_path(fixture, raw["source_file"])
    test_file = _case_path(fixture, raw["focused_test_file"])
    focused_command = _case_command(raw["focused_command"])
    command_args = list(focused_command[3:])
    if command_args and command_args[0] == "discover":
        if "-p" not in command_args:
            raise ValueError("focused discovery command must specify a test pattern")
        pattern_index = command_args.index("-p") + 1
        if pattern_index >= len(command_args):
            raise ValueError("focused discovery pattern is missing")
        if not __import__("fnmatch").fnmatch(Path(test_file).name, command_args[pattern_index]):
            raise ValueError("focused command pattern does not identify the configured test")
    elif Path(test_file).with_suffix("").as_posix().replace("/", ".") not in command_args:
        raise ValueError("focused command module must identify the configured test")
    full_command = shlex.split(REQUIRED_COMMAND)
    evidence_files = raw["evidence_files"]
    if (not isinstance(evidence_files, dict) or not evidence_files
            or len(evidence_files) > 8):
        raise ValueError("invalid case-profile evidence files")
    safe_evidence: dict[str, str] = {}
    for name, relative in evidence_files.items():
        if not isinstance(name, str) or not SAFE_ID.fullmatch(name):
            raise ValueError("invalid case-profile evidence id")
        safe_evidence[name] = _case_path(fixture, relative)
    markers = raw["evidence_markers"]
    if not isinstance(markers, dict) or set(markers) != set(safe_evidence):
        raise ValueError("case-profile evidence markers must match evidence files")
    safe_markers: dict[str, tuple[str, ...]] = {}
    for name, values in markers.items():
        if (not isinstance(values, list) or not values or len(values) > 4
                or any(not isinstance(value, str) or not value.strip() or len(value) > 200
                       for value in values)):
            raise ValueError("invalid case-profile evidence marker")
        safe_markers[name] = tuple(values)
    failure_markers = raw["failure_markers"]
    if (not isinstance(failure_markers, list) or not failure_markers or len(failure_markers) > 4
            or any(not isinstance(value, str) or not value.strip() or len(value) > 200
                   for value in failure_markers)):
        raise ValueError("invalid case-profile failure signature")
    triage = raw["triage"]
    if not isinstance(triage, dict) or set(triage) != {
        "kinds", "hypotheses", "accepted_ids", "accepted_statuses", "observations",
        "rank_hypotheses",
    }:
        raise ValueError("invalid case-profile triage")
    from jevcompass.triage import FailureKind, HypothesisId
    kinds, hypotheses, accepted = (triage[key] for key in ("kinds", "hypotheses", "accepted_ids"))
    statuses = triage["accepted_statuses"]
    observations = triage["observations"]
    kind_values = {item.value for item in FailureKind}
    hypothesis_values = {item.value for item in HypothesisId}
    for values, allowed, label in (
        (kinds, kind_values, "kind"), (hypotheses, hypothesis_values, "hypothesis"),
        (accepted, hypothesis_values, "accepted id"),
    ):
        if (not isinstance(values, list) or not values or len(values) > 4
                or any(not isinstance(value, str) or value not in allowed for value in values)
                or len(set(values)) != len(values)):
            raise ValueError(f"invalid case-profile {label}")
    # Accepted diagnostic actions are a separate configured set: they need
    # not be causal hypotheses and are never added to requested rank orders.
    if (not isinstance(statuses, list) or not statuses or len(statuses) > 1
            or any(not isinstance(value, str) or value != "no-remote-choice" for value in statuses)
            or len(set(statuses)) != len(statuses)):
        raise ValueError("invalid case-profile accepted status")
    from jevcompass.triage import (
        AssertionObservation, ImportObservation, TimeoutObservation,
    )
    observation_enums = {
        "import": ImportObservation, "assertion": AssertionObservation,
        "timeout": TimeoutObservation,
    }
    safe_observations: dict[str, tuple[str, ...]] = {}
    if not isinstance(observations, dict) or set(observations) - set(observation_enums):
        raise ValueError("invalid case-profile observations")
    for key, values in observations.items():
        allowed_values = {item.value for item in observation_enums[key]}
        if (not isinstance(values, list) or not values or len(values) > 4
                or any(not isinstance(value, str) or value not in allowed_values for value in values)
                or len(set(values)) != len(values)):
            raise ValueError("invalid case-profile observation enum")
        safe_observations[key] = tuple(values)
    rank_hypotheses = triage["rank_hypotheses"]
    if type(rank_hypotheses) is not bool:
        raise ValueError("invalid case-profile ranking opt-in")
    allow_remote = False
    outcome = raw["outcome_mode"]
    if outcome not in {"contract_triage", "repair"}:
        raise ValueError("invalid case-profile outcome mode")
    oracle_rel = raw["oracle_script"]
    if (not isinstance(oracle_rel, str) or not oracle_rel
            or Path(oracle_rel).is_absolute() or ".." in oracle_rel.split("/")
            or "\\\\" in oracle_rel):
        raise ValueError("invalid supervisor oracle path")
    oracle_script = ROOT / oracle_rel
    if any(part in {"", ".", ".."} for part in oracle_rel.split("/")):
        raise ValueError("invalid supervisor oracle path")
    if any((ROOT / Path(*Path(oracle_rel).parts[:index])).is_symlink()
           for index in range(1, len(Path(oracle_rel).parts) + 1)):
        raise ValueError("supervisor oracle cannot traverse symlinks")
    if (not oracle_script.is_file() or oracle_script.is_symlink()
            or fixture in oracle_script.resolve().parents):
        raise ValueError("oracle must be an immutable supervisor-owned script")
    expected_hash = raw["oracle_sha256"]
    if (not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
            or hashlib.sha256(oracle_script.read_bytes()).hexdigest() != expected_hash):
        raise ValueError("supervisor oracle hash does not match profile")
    return CaseProfile(
        case_id, fixture, prompt, source_file, test_file, focused_command,
        safe_evidence, safe_markers, tuple(failure_markers), tuple(kinds),
        tuple(hypotheses), tuple(accepted), tuple(statuses), safe_observations,
        rank_hypotheses, outcome, oracle_script, expected_hash,
    )


def _profile_bridge_spec(profile: CaseProfile) -> profile_bridge.ProfileTriageSpec:
    hypotheses = list(profile.triage_hypotheses)
    for identifier in profile.triage_accepted_ids:
        if identifier not in hypotheses:
            hypotheses.append(identifier)
    return profile_bridge.ProfileTriageSpec(
        failure_kinds=profile.triage_kinds,
        hypotheses=tuple(hypotheses),
        allowed_observations=profile.triage_observations,
        rank_hypotheses=profile.rank_hypotheses,
    )


def _case_treatment_prompt(profile: CaseProfile, advice_policy: str) -> str:
    evidence_paths = ", ".join(profile.evidence_files.values())
    triage_command = shlex.join(_triage_argv(1, profile))
    guidance = (
        f"\n\nAfter the initial focused test, inspect these configured evidence files: {evidence_paths}. "
        "Use only the locally observed exit code and allowlisted enums in any JevCompass triage command; "
        "never pass file contents, paths, diagnostics, or source text to triage. "
        "Triage is optional: skip it when local evidence resolves the choice. "
        f"If you invoke triage, use this exact command: {triage_command}. "
    )
    if profile.outcome_mode == "repair":
        guidance += (
            f"Complete the requested behavior change in {profile.source_file}. "
            f"Do not modify {profile.focused_test_file} or configured evidence files. "
            "Run git diff --check and require exit code zero after the change. "
            "Run the focused test again and the required full test suite after the change. "
            "The supervisor runs a separate immutable oracle."
        )
    else:
        guidance += (
            "Preserve the original test result and do not guess when the available evidence "
            "does not establish the expected behavior."
        )
    if advice_policy == "nonbinding":
        return (
            profile.task_prompt + "\n\nBefore using a tool, make your first assistant message contain this "
            "exact line: " + WORKFLOW_ACK_LINE
            + " If you request triage, after its result and before using another tool, "
            + "make your next assistant message contain this exact line: "
            + PROFILE_TRIAGE_RESULT_ACK_LINE + guidance
        )
    return profile.task_prompt + guidance


def _matches_useful_failure(output: Any, profile: CaseProfile | None = None) -> bool:
    if not isinstance(output, str) or len(output.encode("utf-8")) > MAX_ANSWER_BYTES:
        return False
    markers = profile.failure_markers if profile else (
        "FAIL: test_invoice_total_matches_legacy_golden",
        "AssertionError: Decimal('0.02') != Decimal('0.01')",
    )
    return all(marker in output for marker in markers)
_REQUIRED_EVIDENCE = frozenset(EVIDENCE_FILES)


def _item(event: dict[str, Any]) -> dict[str, Any] | None:
    return common._item(event)


def _focused(item: dict[str, Any], profile: CaseProfile | None = None) -> bool:
    command = profile.focused_command if profile else tuple(shlex.split(FOCUSED_COMMAND))
    return common._command_argv(item) == list(command)


def _full(item: dict[str, Any], profile: CaseProfile | None = None) -> bool:
    return common._command_argv(item) == shlex.split(REQUIRED_COMMAND)


def _triage_argv(exit_code: int, profile: CaseProfile | None = None) -> list[str]:
    if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code == 0:
        raise ValueError("triage requires an observed nonzero focused exit")
    if profile is None:
        suffix = list(TRIAGE_SUFFIX)
    else:
        suffix = []
        for kind in profile.triage_kinds:
            suffix.extend(("--kind", kind))
        candidates = list(profile.triage_hypotheses)
        if ("confirm_behavior_contract" in profile.triage_accepted_ids
                and "confirm_behavior_contract" not in candidates):
            candidates.append("confirm_behavior_contract")
        for hypothesis in candidates:
            suffix.extend(("--hypothesis", hypothesis))
        if profile.rank_hypotheses:
            suffix.append("--rank-hypotheses")
        observation_flags = {
            "import": "--import-observation",
            "assertion": "--assertion-observation",
            "timeout": "--timeout-observation",
        }
        for category, values in profile.triage_observations.items():
            for value in values:
                suffix.extend((observation_flags[category], value))
        suffix.append("--json")
    return [*TRIAGE_PREFIX, str(exit_code), *suffix]


def _is_triage(item: dict[str, Any], exit_code: int | None,
               profile: CaseProfile | None = None) -> bool:
    return exit_code is not None and exit_code != 0 and (
        common._command_argv(item) == _triage_argv(exit_code, profile)
    )


def _evidence_kinds(item: dict[str, Any], profile: CaseProfile | None = None) -> tuple[str, ...]:
    argv = common._command_argv(item) or []
    joined = " ".join(argv)
    files = profile.evidence_files if profile else EVIDENCE_FILES
    return tuple(kind for kind, relative in files.items() if relative in joined)


def _evidence_confirms(kind: str, output: Any,
                       profile: CaseProfile | None = None) -> bool:
    if not isinstance(output, str) or len(output.encode("utf-8")) > MAX_ANSWER_BYTES:
        return False
    if profile is not None:
        markers = profile.evidence_markers.get(kind, ())
        lowered = output.lower()
        return bool(markers) and all(marker.lower() in lowered for marker in markers)
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


def _validated_triage(
    payload: Any, focused_exit: int, profile: CaseProfile | None = None,
    accepted_statuses: tuple[str, ...] | None = None,
    bridge_decision_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (not isinstance(payload, str)
            or len(payload.encode("utf-8")) > triage_common.MAX_TRIAGE_OUTPUT_BYTES):
        return {"status": "unscored", "candidate_ids": []}
    try:
        value = _strict_json(payload)
    except (ValueError, TypeError, json.JSONDecodeError):
        return {"status": "unscored", "candidate_ids": []}
    if not isinstance(value, dict) or value.get("executed") is not False:
        return {"status": "unscored", "candidate_ids": []}
    allowed_ids = profile.triage_accepted_ids if profile else TRIAGE_IDS
    accepted_statuses = (
        accepted_statuses if accepted_statuses is not None else
        profile.triage_accepted_statuses if profile else ("no-remote-choice",)
    )
    parsed = parse_choice_receipt(
        value, choice_type="triage", candidate_ids=allowed_ids,
    )
    if (
        parsed.get("status") not in accepted_statuses
        or not parsed.get("candidate_ids")
        or any(item not in allowed_ids for item in parsed["candidate_ids"])
        or parsed.get("observed_exit_status") != focused_exit
        or value.get("test_failed") is not True
    ):
        return {"status": "unscored", "candidate_ids": []}
    if parsed.get("status") == "remote-choice":
        remote_confirmed = (
            isinstance(bridge_decision_receipt, dict)
            and bridge_decision_receipt.get("status") == "remote-choice"
            and bridge_decision_receipt.get("observed_exit_status") == focused_exit
            and bridge_decision_receipt.get("test_failed") is True
            and bridge_decision_receipt.get("executed") is False
            and bridge_decision_receipt.get("decision_reason") == "accepted"
            and bridge_decision_receipt.get("diagnostic_choice_id") == parsed["candidate_ids"][0]
            and bridge_decision_receipt.get("diagnostic_step_ids") == parsed["candidate_ids"]
            and bridge_decision_receipt.get("diagnostic_selection_source") in {
                "cached_preferred_next_step", "remote_preferred_next_step",
            }
        )
        if not remote_confirmed:
            return {"status": "unscored", "candidate_ids": []}
    parsed["hypothesis_order"] = []
    parsed["hypothesis_ranking_status"] = "not_established"
    if profile is not None and profile.rank_hypotheses:
        order = value.get("hypothesis_order")
        status = value.get("hypothesis_ranking_status")
        causal_ids = {
            item for item in profile.triage_hypotheses
            if item != "confirm_behavior_contract"
        }
        if (status == "complete" and isinstance(order, list)
                and all(isinstance(item, str) for item in order)
                and len(order) == len(causal_ids)
                and len(set(order)) == len(order)
                and set(order) == causal_ids and len(causal_ids) >= 2):
            parsed["hypothesis_ranking_status"] = "complete"
            parsed["hypothesis_order"] = list(order)
        elif status == "not_established" and order == []:
            parsed["hypothesis_ranking_status"] = "not_established"
        else:
            # A valid next step remains valid independently of an incomplete
            # or malformed causal ordering; never promote that order to success.
            parsed["hypothesis_ranking_status"] = "incomplete"
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
    profile: CaseProfile | None = None,
    accepted_triage_statuses: tuple[str, ...] | None = None,
    profile_bridge_receipt: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    lines = list(lines)
    pending: dict[str, tuple[str, str | None]] = {}
    evidence: set[str] = set()
    focused_exits: list[int] = []
    full_exits: list[int] = []
    git_diff_check_exits: list[int] = []
    git_diff_check_invoked = False
    useful_failure_ms: float | None = None
    useful_failure_observed = False
    triage_seen = False
    triage_after_evidence = False
    triage_invalid = False
    triage_exit: int | None = None
    triage_choice = {"status": "unscored", "candidate_ids": [],
                     "hypothesis_ranking_status": "not_established",
                     "hypothesis_order": []}
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
                        and (TRIAGE_RESULT_ACK_LINE if profile is None else
                             PROFILE_TRIAGE_RESULT_ACK_LINE)
                        in [line.strip() for line in message.splitlines()]
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
            if _focused(item, profile):
                kind = "focused"
            elif _full(item, profile):
                kind = "full"
            else:
                evidence_kinds = _evidence_kinds(item, profile)
                if evidence_kinds:
                    kind = "evidence:" + ",".join(evidence_kinds)
            if kind:
                pending[event_id] = (kind, None)
            argv = common._command_argv(item)
            if (profile is not None and profile.outcome_mode == "repair"
                    and argv == ["git", "diff", "--check"]):
                git_diff_check_invoked = True
                pending[event_id] = ("git-diff-check", None)
            if argv and "jevcompass" in argv:
                triage_seen = True
                exact = _is_triage(item, focused_exits[0] if focused_exits else None, profile)
                required_evidence = frozenset(profile.evidence_files) if profile else _REQUIRED_EVIDENCE
                evidence_before_triage = required_evidence.issubset(evidence)
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
            if kind == "git-diff-check" and isinstance(code, int) and not isinstance(code, bool):
                git_diff_check_exits.append(code)
            elif kind == "focused" and isinstance(code, int) and not isinstance(code, bool):
                focused_exits.append(code)
                output = item.get("aggregated_output")
                if not isinstance(output, str):
                    output = item.get("output")
                if (len(focused_exits) == 1 and code == 1
                        and _matches_useful_failure(output, profile)):
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
                    if _evidence_confirms(evidence_kind, output, profile):
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
                        bridge_decision = (
                            profile_bridge_receipt.get("decision")
                            if isinstance(profile_bridge_receipt, dict) else None
                        )
                        triage_choice = _validated_triage(
                            output, focused_exits[0], profile,
                            accepted_statuses=accepted_triage_statuses,
                            bridge_decision_receipt=bridge_decision,
                        )
                        if not triage_choice.get("candidate_ids"):
                            triage_output_status = "invalid_output"
                        elif profile is None:
                            triage_output_status = "valid_local_step"
                        else:
                            triage_output_status = "valid_configured_choice"
                        if triage_output_status in {
                            "valid_local_step", "valid_configured_choice",
                        }:
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
    if profile_bridge_receipt is not None:
        decision = profile_bridge_receipt.get("decision")
        if not isinstance(decision, dict):
            decision = {}
        result["profile_triage_bridge"] = profile_bridge_receipt
        result["provider_transport_call_count"] = decision.get(
            "provider_transport_call_count", 0
        )
        result["decision_usage_status"] = decision.get(
            "decision_usage_status", "not_invoked"
        )
        result["decision_usage"] = decision.get("decision_usage")
        result["diagnostic_choice_id"] = decision.get("diagnostic_choice_id")
        result["diagnostic_selection_source"] = decision.get(
            "diagnostic_selection_source", "none"
        )
        result["hypothesis_ranking_status"] = decision.get(
            "hypothesis_ranking_status", "not_established"
        )
        result["hypothesis_order"] = decision.get("hypothesis_order", [])
    if profile is not None and profile.outcome_mode == "repair":
        result["agent_git_diff_check_invocation_observed"] = git_diff_check_invoked
        result["agent_git_diff_check_exit_codes"] = git_diff_check_exits
        result["agent_git_diff_check_exit_code"] = (
            git_diff_check_exits[-1] if git_diff_check_exits else None
        )
        result["agent_git_diff_check_passed"] = bool(
            git_diff_check_invoked and git_diff_check_exits
            and all(code == 0 for code in git_diff_check_exits)
        )
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
    profile: CaseProfile | None = None,
    profile_bridge_spec: profile_bridge.ProfileTriageSpec | None = None,
    accepted_triage_statuses: tuple[str, ...] | None = None,
    allow_network: bool = False,
    max_tokens: int | None = None,
) -> tuple[dict[str, Any], str | None]:
    (home / ".codex").mkdir(mode=0o700, parents=True, exist_ok=True)
    measurement_path = measurement_path or (home / ".codex" / "agent-measurement.json")
    env = core._isolated_environment(
        home=home, isolated_python=home / "python", allow_openrouter_key=False,
    )
    env.pop("OPENROUTER_API_KEY", None)
    started = time.monotonic()
    bridge = None
    bridge_receipt_path: Path | None = None
    bridge_summary: dict[str, Any] | None = None
    typed_bridge_receipt: dict[str, Any] | None = None
    process = None
    try:
        if profile_bridge_spec is not None:
            if not treatment or profile is None:
                raise ValueError("profile bridge is treatment-only and requires a case profile")
            bridge_receipt_path = measurement_path.with_name(
                measurement_path.stem + "-profile-triage.json"
            )
            bridge = profile_bridge.ProfileTriageBridge(
                profile_bridge_spec, receipt_path=bridge_receipt_path,
            )
            with bridge:
                shim_path = profile_bridge.write_python_shim(
                    home / "profile-triage-bridge", bridge,
                )
                env["PATH"] = os.pathsep.join(
                    item for item in (str(shim_path), env.get("PATH", "")) if item
                )
                process = subprocess.Popen(
                    common._cli_command(
                        codex, model, reasoning_effort, prompt,
                        allow_network=allow_network,
                    ),
                    cwd=fixture, env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
                focused_ids: set[str] = set()
                focused_seen = False

                def observe(event: dict[str, Any]) -> None:
                    nonlocal focused_seen
                    event_type = event.get("type")
                    item = _item(event)
                    if item is None:
                        return
                    event_id = item.get("id")
                    if not isinstance(event_id, str):
                        return
                    if event_type == "item.started" and _focused(item, profile):
                        focused_ids.add(event_id)
                    elif (event_type == "item.completed" and event_id in focused_ids
                          and not focused_seen):
                        focused_seen = True
                        code = item.get("exit_code")
                        output = item.get("aggregated_output")
                        if not isinstance(output, str):
                            output = item.get("output")
                        confirmed = (
                            isinstance(code, int) and not isinstance(code, bool)
                            and code != 0 and _matches_useful_failure(output, profile)
                        )
                        bridge.observe_focused_failure(
                            code if isinstance(code, int) and not isinstance(code, bool) else 0,
                            failure_confirmed=confirmed,
                        )

                lines, times, failure = core._collect_events(
                    process, started=started, timeout=timeout,
                    preserve_on_failure=True, max_tokens=max_tokens,
                    event_observer=observe,
                )
                bridge_summary = bridge.receipt()
        else:
            process = subprocess.Popen(
                common._cli_command(
                    codex, model, reasoning_effort, prompt,
                    allow_network=allow_network,
                ),
                cwd=fixture, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            lines, times, failure = core._collect_events(
                process, started=started, timeout=timeout,
                preserve_on_failure=True, max_tokens=max_tokens,
            )
    except Exception:
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            except (OSError, subprocess.SubprocessError):
                pass
        if bridge is not None:
            bridge_summary = bridge.receipt()
        failed = _empty_arm(
            "profile_bridge_setup_failed" if profile_bridge_spec else "codex_unavailable"
        )
        failed["profile_triage_bridge"] = bridge_summary
        return failed, None

    ended = time.monotonic()
    wall_ms = round((ended - started) * 1000, 2)
    measurement = build_agent_measurement_receipt(
        lines, times, started, ended, process.returncode, failure,
    )
    # Preserve the bounded event measurement before task-specific parsing.
    common._private_write(
        measurement_path,
        (json.dumps(measurement, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
    )

    if bridge_receipt_path is not None and bridge_receipt_path.is_file():
        try:
            if bridge_receipt_path.stat().st_size <= profile_bridge.MAX_RESPONSE_BYTES:
                loaded = _strict_json(bridge_receipt_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    typed_bridge_receipt = loaded
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            typed_bridge_receipt = None

    bridge_metadata = {}
    if bridge_summary is not None:
        decision = bridge_summary.get("decision")
        if isinstance(decision, dict):
            bridge_metadata = {
                "provider_transport_call_count": decision.get("provider_transport_call_count", 0),
                "decision_usage_status": decision.get("decision_usage_status", "not_invoked"),
                "decision_usage": decision.get("decision_usage"),
                "diagnostic_choice_id": decision.get("diagnostic_choice_id"),
                "diagnostic_selection_source": decision.get("diagnostic_selection_source", "none"),
                "hypothesis_order": decision.get("hypothesis_order", []),
                "hypothesis_ranking_status": decision.get("hypothesis_ranking_status", "not_established"),
            }

    if failure is not None:
        failed = _empty_arm(failure)
        failed["completion_ms"] = wall_ms if wall_ms <= MAX_TIMEOUT * 1000 else None
        failed["cli_exit_code"] = process.returncode
        failed["agent_measurement"] = measurement
        failed["event_count"] = len(lines)
        if bridge_summary is not None:
            failed["profile_triage_bridge"] = bridge_summary
            failed["profile_triage_typed_receipt"] = typed_bridge_receipt
            failed.update(bridge_metadata)
        return failed, None

    try:
        observed, answer = _event_receipts(
            lines, times, started, advice_policy=advice_policy, profile=profile,
            accepted_triage_statuses=accepted_triage_statuses,
            profile_bridge_receipt=bridge_summary,
        )
    except Exception:
        failed = _empty_arm("event_parser_error")
        failed.update({
            "completion_ms": wall_ms if wall_ms <= MAX_TIMEOUT * 1000 else None,
            "cli_exit_code": process.returncode,
            "agent_measurement": measurement,
            "event_count": len(lines),
            "profile_triage_bridge": bridge_summary,
            "profile_triage_typed_receipt": typed_bridge_receipt,
            **bridge_metadata,
        })
        return failed, None

    result = {
        "cli_status": "completed" if process.returncode == 0 else "failed",
        "failure": None if process.returncode == 0 else "cli_exit_nonzero",
        "cli_exit_code": process.returncode,
        "completion_ms": wall_ms if wall_ms <= MAX_TIMEOUT * 1000 else None,
        "agent_measurement": measurement,
        **observed,
    }
    if bridge_summary is not None:
        result["profile_triage_bridge"] = bridge_summary
        result["profile_triage_typed_receipt"] = typed_bridge_receipt
        result.update(bridge_metadata)
    return result, answer


def _initialize_private_git_baseline(root: Path) -> bool:
    """Initialize an isolated Git baseline for one copied repair fixture."""
    git = shutil.which("git")
    if not git:
        return False
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "JevCompass Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "JevCompass Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    commands = (
        [git, "init", "--quiet"],
        [git, "add", "-A"],
        [git, "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
         "commit", "--quiet", "-m", "Immutable synthetic fixture baseline"],
    )
    try:
        for argv in commands:
            completed = subprocess.run(
                argv, cwd=root, env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10, check=False,
            )
            if completed.returncode != 0:
                return False
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


def _repair_git_diff_check_valid(arm: dict[str, Any]) -> bool:
    """Require an observed, successful diff check for repair-profile completion."""
    exits = arm.get("agent_git_diff_check_exit_codes")
    return bool(
        arm.get("agent_git_diff_check_invocation_observed") is True
        and isinstance(exits, list) and exits
        and all(isinstance(code, int) and not isinstance(code, bool) and code == 0
                for code in exits)
        and arm.get("agent_git_diff_check_passed") is True
    )


def _fixture_digest(
    root: Path, *, exclude_relative: str | None = None,
    exclude_internal_git: bool = False,
) -> str | None:
    """Hash fixture files while ignoring bytecode and optional private .git metadata."""
    digest = hashlib.sha256()
    try:
        for path in sorted(root.rglob("*")):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            relative_text = path.relative_to(root).as_posix()
            if relative_text == exclude_relative:
                continue
            if exclude_internal_git and (relative_text == ".git" or relative_text.startswith(".git/")):
                continue
            relative = relative_text.encode("utf-8")
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


def _run_independent_oracle(
    profile: CaseProfile, fixture: Path, timeout: int,
) -> dict[str, Any]:
    started = time.monotonic()
    evidence: dict[str, Any] = {
        "status": "unscored",
        "exit_code": None,
        "elapsed_ms": None,
        "oracle_sha256": profile.oracle_sha256,
        "oracle_unchanged": False,
    }
    try:
        before = hashlib.sha256(profile.oracle_script.read_bytes()).hexdigest()
        if before != profile.oracle_sha256:
            return evidence
        completed = subprocess.run(
            [sys.executable, str(profile.oracle_script), "--fixture-dir", str(fixture)],
            cwd=ROOT, env={}, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False,
        )
        after = hashlib.sha256(profile.oracle_script.read_bytes()).hexdigest()
        evidence["oracle_unchanged"] = after == profile.oracle_sha256
        evidence["exit_code"] = completed.returncode
        evidence["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        if evidence["oracle_unchanged"] and completed.returncode == 0:
            evidence["status"] = "passed"
        elif evidence["oracle_unchanged"]:
            evidence["status"] = "failed"
    except (OSError, subprocess.TimeoutExpired):
        evidence["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
    return evidence


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
    case_profile: CaseProfile | None = None,
    remote_profile_triage: bool = False,
    accepted_remote_statuses: tuple[str, ...] = (),
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Run both local-only CLI arms and save private blind artifacts/receipts."""
    if case_profile is not None:
        fixture_source = case_profile.fixture_source
    source_file_rel = case_profile.source_file if case_profile else SOURCE_FILE
    test_file_rel = case_profile.focused_test_file if case_profile else TEST_FILE
    required_evidence = frozenset(case_profile.evidence_files) if case_profile else _REQUIRED_EVIDENCE
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if not fixture_source.is_dir():
        raise FileNotFoundError("synthetic contract fixture is unavailable")
    if (not SAFE_MODEL.fullmatch(model) or not SAFE_ID.fullmatch(reasoning_effort)):
        raise ValueError("model and reasoning effort must be simple identifiers")
    if not isinstance(advice_policy, str) or advice_policy not in ADVICE_POLICIES:
        raise ValueError("invalid advice policy")
    if type(remote_profile_triage) is not bool:
        raise ValueError("remote_profile_triage must be boolean")
    if remote_profile_triage:
        if case_profile is None:
            raise ValueError("remote profile triage requires a validated case profile")
        allowed_remote_statuses = {"remote-choice", "no-remote-choice"}
        if (not isinstance(accepted_remote_statuses, tuple)
                or not accepted_remote_statuses
                or len(set(accepted_remote_statuses)) != len(accepted_remote_statuses)
                or any(value not in allowed_remote_statuses for value in accepted_remote_statuses)):
            raise ValueError("explicit supervisor acceptance statuses are required")
    elif accepted_remote_statuses:
        raise ValueError("accepted remote statuses require explicit remote profile triage")
    if max_tokens is not None and (
        isinstance(max_tokens, bool) or not isinstance(max_tokens, int)
        or not 1 <= max_tokens <= core.MAX_EVENT_TOKEN_BUDGET
    ):
        raise ValueError("max_tokens is outside the supported event-token budget")
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
            for name in (source_file_rel, test_file_rel)
        }
        initial_fixture_digest = _fixture_digest(fixture_source)
        repair_profile = bool(case_profile and case_profile.outcome_mode == "repair")
        initial_protected_digest = _fixture_digest(
            fixture_source, exclude_relative=source_file_rel,
            exclude_internal_git=repair_profile,
        ) if repair_profile else initial_fixture_digest
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
        if repair_profile and not all(
            _initialize_private_git_baseline(fixtures[name])
            for name in ("baseline", "treatment")
        ):
            receipt = {
                "schema_version": 1, "run_id": run_id, "status": "failed",
                "failure": "repair_git_setup_failed", "arms": {},
            }
            common._private_write(
                output_dir / "receipt.json",
                (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8"),
            )
            return receipt

        bridge_spec = _profile_bridge_spec(case_profile) if remote_profile_triage else None
        arms: dict[str, dict[str, Any]] = {}
        answers: dict[str, str | None] = {}
        for true_arm in order:
            label = labels[true_arm]
            prompt = (BASE_PROMPT if case_profile is None else case_profile.task_prompt)
            if true_arm == "treatment":
                prompt = (
                    NONBINDING_TREATMENT_PROMPT if case_profile is None and advice_policy == "nonbinding"
                    else TREATMENT_PROMPT if case_profile is None
                    else _case_treatment_prompt(case_profile, advice_policy)
                )
            arms[label], answers[label] = _run_arm(
                codex=codex, model=model, reasoning_effort=reasoning_effort,
                prompt=prompt, fixture=fixtures[true_arm], home=homes[true_arm],
                timeout=timeout, treatment=true_arm == "treatment",
                advice_policy=(advice_policy if true_arm == "treatment"
                               else "legacy-required-step"),
                measurement_path=output_dir / f"{label}-agent-measurement.json",
                profile=case_profile,
                profile_bridge_spec=bridge_spec if true_arm == "treatment" else None,
                accepted_triage_statuses=(
                    accepted_remote_statuses
                    if remote_profile_triage and true_arm == "treatment" else None
                ),
                allow_network=remote_profile_triage,
                max_tokens=max_tokens,
            )
            if repair_profile:
                arms[label].setdefault("agent_git_diff_check_invocation_observed", False)
                arms[label].setdefault("agent_git_diff_check_exit_codes", [])
                arms[label].setdefault("agent_git_diff_check_exit_code", None)
                arms[label].setdefault("agent_git_diff_check_passed", False)
            arms[label]["fixture_sha256_before"] = digests[true_arm]

        source_unchanged: dict[str, bool] = {}
        source_changed: dict[str, bool] = {}
        tests_unchanged: dict[str, bool] = {}
        fixture_unchanged: dict[str, bool] = {}
        protected_fixture_unchanged: dict[str, bool] = {}
        for true_arm in ("baseline", "treatment"):
            label = labels[true_arm]
            for relative, target in ((source_file_rel, source_unchanged), (test_file_rel, tests_unchanged)):
                final = _safe_artifact(fixtures[true_arm] / relative)
                original = initial_sources.get(relative)
                target[label] = final is not None and original is not None and final == original
                if relative == source_file_rel:
                    source_changed[label] = (
                        final is not None and original is not None and final != original
                    )
            fixture_unchanged[label] = (
                initial_fixture_digest is not None
                and _fixture_digest(fixtures[true_arm]) == initial_fixture_digest
            )
            protected_fixture_unchanged[label] = (
                initial_protected_digest is not None
                and _fixture_digest(
                    fixtures[true_arm], exclude_relative=source_file_rel,
                    exclude_internal_git=repair_profile,
                ) == initial_protected_digest
            )
            source_bytes = _safe_artifact(fixtures[true_arm] / source_file_rel)
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

        if case_profile is not None:
            for true_arm in ("baseline", "treatment"):
                label = labels[true_arm]
                validation = _run_independent_oracle(
                    case_profile, fixtures[true_arm], timeout,
                )
                arm = arms[label]
                arm["independent_oracle"] = validation
                endpoint_ms = None
                if (arm.get("cli_status") == "completed"
                        and validation.get("status") == "passed"
                        and isinstance(arm.get("completion_ms"), (int, float))
                        and isinstance(validation.get("elapsed_ms"), (int, float))):
                    endpoint_ms = round(arm["completion_ms"] + validation["elapsed_ms"], 2)
                arm["validated_endpoint_ms"] = endpoint_ms
                completion_valid = bool(
                    case_profile.outcome_mode == "repair"
                    and endpoint_ms is not None
                    and arm.get("first_useful_failure_observed") is True
                    and 0 in arm.get("focused_exit_codes", [])[1:]
                    and arm.get("full_suite_exit") == 0
                    and source_changed.get(label) is True
                    and tests_unchanged.get(label) is True
                    and protected_fixture_unchanged.get(label) is True
                    and _repair_git_diff_check_valid(arm)
                )
                arm["validated_completion_ms"] = endpoint_ms if completion_valid else None
                arm["task_outcome_status"] = (
                    "completed" if completion_valid else "incomplete"
                ) if case_profile.outcome_mode == "repair" else "not_applicable"

        protocol_complete = all(
            arms.get(label, {}).get("cli_status") == "completed"
            and arms[label].get("initial_focused_exit") == 1
            and arms[label].get("full_suite_invocation_observed") is True
            and tests_unchanged.get(label) is True
            and (
                protected_fixture_unchanged.get(label) is True
                if case_profile and case_profile.outcome_mode == "repair"
                else (
                    arms[label].get("full_suite_exit") == 1
                    and source_unchanged.get(label) is True
                    and fixture_unchanged.get(label) is True
                )
            )
            for label in ("arm-a", "arm-b")
        )
        baseline_label = "arm-a" if mapping["arm-a"] == "baseline" else "arm-b"
        baseline = arms.get(baseline_label, {})
        if case_profile and case_profile.outcome_mode == "repair":
            baseline_gate = baseline.get("task_outcome_status") == "completed"
        else:
            baseline_gate = (
                baseline.get("first_useful_failure_observed") is True
                and baseline.get("triage_invocation_observed") is False
            )
        treatment_label = "arm-a" if mapping["arm-a"] == "treatment" else "arm-b"
        treatment = arms.get(treatment_label, {})
        if case_profile and case_profile.outcome_mode == "repair":
            treatment_gate = treatment.get("task_outcome_status") == "completed"
        else:
            treatment_gate = (
                treatment.get("first_useful_failure_observed") is True
                and treatment.get("evidence_complete_before_triage") is True
                and treatment.get("triage_after_evidence") is True
                and treatment.get("triage_invalid_invocation_observed") is False
                and treatment.get("triage_exit_code") == 0
                and treatment.get("triage_output_status") == (
                    "valid_local_step" if case_profile is None else "valid_configured_choice"
                )
            )
        repair_task_correctness = bool(
            case_profile and case_profile.outcome_mode == "repair"
            and protocol_complete and baseline_gate and treatment_gate
        )
        if advice_policy == "nonbinding":
            workflow_acknowledged = (
                treatment.get("workflow_acknowledgment") == "before_first_tool"
            )
            verified_evidence = set(treatment.get("evidence_categories_verified", [])) == set(
                required_evidence
            )
            valid_triage = (
                treatment.get("triage_after_evidence") is True
                and treatment.get("triage_invalid_invocation_observed") is False
                and treatment.get("triage_exit_code") == 0
                and treatment.get("triage_output_status") in {
                    "valid_local_step", "valid_configured_choice",
                }
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
        if case_profile and case_profile.outcome_mode == "repair":
            # Task acceptance is symmetric and independent of optional advice.
            task_correctness = repair_task_correctness
            treatment_gate = treatment.get("task_outcome_status") == "completed"
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
            "case_profile_id": case_profile.case_id if case_profile else None,
            "outcome_mode": case_profile.outcome_mode if case_profile else "legacy_contract_triage",
            "decision_scope": (
                "local_fixture_triage_no_remote_choice" if case_profile is None
                else "case_profile_profile_bridge_remote_possible"
                if remote_profile_triage else "case_profile_local_triage_remote_unavailable"
            ),
            "remote_profile_triage_enabled": remote_profile_triage,
            "accepted_remote_statuses": list(accepted_remote_statuses),
            "network_access_enabled_for_both_arms": remote_profile_triage,
            "observed_token_budget": max_tokens,
            "task_outcome_acceptance_status": (
                "passed" if case_profile and case_profile.outcome_mode == "repair"
                and repair_task_correctness else "failed" if case_profile and case_profile.outcome_mode == "repair"
                else "not_applicable"
            ),
            "advice_adoption_status": "not_measured",
            "original_failed_exit_preserved": all(
                arms.get(label, {}).get("initial_focused_exit") == 1
                for label in ("arm-a", "arm-b")
            ),
            "source_unchanged": source_unchanged,
            "tests_unchanged": tests_unchanged,
            "fixture_unchanged_excluding_bytecode": fixture_unchanged,
            "protected_fixture_unchanged_excluding_source": protected_fixture_unchanged,
            "source_changed": source_changed,
            "baseline_gate_observed": baseline_gate,
            "blind_artifacts": {
                label: {
                    "source_captured": (output_dir / f"{label}-source.py").is_file(),
                    "final_captured": (output_dir / f"{label}-final.txt").is_file(),
                    "agent_measurement_captured": (
                        output_dir / f"{label}-agent-measurement.json"
                    ).is_file(),
                    "profile_triage_typed_receipt_captured": (
                        output_dir / f"{label}-agent-measurement-profile-triage.json"
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
                "decision_scope": (
                    "local_fixture_triage_no_remote_choice" if case_profile is None
                    else "case_profile_profile_bridge_remote_possible"
                    if remote_profile_triage else "case_profile_local_triage_remote_unavailable"
                ),
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
    parser.add_argument("--case-profile", type=Path,
                        help="validated JSON case profile for a new synthetic fixture")
    parser.add_argument("--advice-policy", choices=ADVICE_POLICIES,
                        default="legacy-required-step",
                        help="nonbinding is an opt-in profile; legacy remains the default")
    parser.add_argument("--remote-profile-triage", action="store_true",
                        help="opt in to a treatment-only local bridge for a validated profile")
    parser.add_argument("--accept-profile-triage-status", action="append",
                        choices=("remote-choice", "no-remote-choice"), default=[],
                        help="explicit supervisor acceptance status (repeat as needed)")
    parser.add_argument("--max-tokens", type=int,
                        help="observed completed-turn token cap; not a provider-side limit")
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
            case_profile=load_case_profile(args.case_profile) if args.case_profile else None,
            remote_profile_triage=args.remote_profile_triage,
            accepted_remote_statuses=tuple(args.accept_profile_triage_status),
            max_tokens=args.max_tokens,
        )
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({"status": "failed", "failure": "runner_setup_failed"}))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
