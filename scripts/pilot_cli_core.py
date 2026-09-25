#!/usr/bin/env python3
"""Run bounded, synthetic-only CLI pairs for JevCompass core cases.

Live mode invokes the same explicit Codex model/settings in randomized
baseline/treatment arms. Use --mock for offline fake-CLI validation.
Receipts contain allowlisted metadata only; no prompt, transcript, source,
command text, authentication material, or model response is retained.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import secrets
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "cli_core"
CORE_CASE_IDS = ("P01", "P03", "P05", "P07", "P08", "R01", "R02", "R03", "R04", "R05", "R06")
CASE_IDS = CORE_CASE_IDS + ("P09",)  # Supplemental CI-guided coding pair.
PUBLISHED_PILOT_VERSION = "0.1.16"
BUNDLED_SKILL_NAMES = ("jevcompass-focused-tests", "jevcompass-regression-review")
PROMPTS = {
    "P01": "Implement normalize_whitespace so empty input stays empty and repeated whitespace is collapsed and trimmed. Run the existing test suite with python -m unittest discover -s tests; it includes tests/test_text.py. Do not add duplicate tests.",
    "P09": "Implement normalize_whitespace in this Python project so empty input stays empty and repeated whitespace is collapsed and trimmed. Follow repository instructions and run its required validation. Do not add duplicate tests.",
    "P03": "Fix scripts/render_report.sh so running it without OUTPUT_PATH uses a safe default or exits with a clear missing-path error before expanding the variable. Run bash -n scripts/render_report.sh.",
    "P05": "Update the Python project README's install instructions to match the current CLI help and existing test behavior.",
    "P07": "Design and write STATUS_API.md as an API contract for the Python POST /status endpoint. Specify required and optional JSON inputs, a validated status result, successful responses, and 4xx and 5xx response cases. Do not implement a server.",
    "P08": "Create a new Python package repository scaffold in a new scaffoldpkg/ directory in this synthetic fixture. Add minimal pyproject.toml metadata, an importable src/scaffoldpkg module, and a focused unittest smoke test in tests/test_smoke.py. Run the test. Do not publish or contact external services.",
    "R01": "Print the current branch name in the synthetic fixture.",
    "R02": "Count the top-level files in the synthetic fixture.",
    "R03": "Check whether README.md exists in the synthetic fixture.",
    "R04": "Show the size of pyproject.toml in bytes in the synthetic fixture.",
    "R05": "List the top-level Python files in the synthetic fixture.",
    "R06": "Check whether README.md contains the word timeout.",
}
READ_ONLY_CASES = frozenset({"R01", "R02", "R03", "R04", "R05", "R06"})
ROUTINE_CASES = frozenset({"R01", "R02", "R03", "R04", "R05", "R06"})
PREFLIGHT_CASES = frozenset({"P01", "P03", "P05", "P07", "P08", "P09"})
PREFLIGHT_INSTRUCTION = (
    "Before your first tool call, report the JevCompass advice ID and only the candidate IDs. "
    "If no advisory is present, report exactly: NO JEVCOMPASS ADVISORY."
)
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 300
MAX_EVENT_BYTES = 4 * 1024 * 1024
MAX_USAGE_COUNTER = 1_000_000_000
TURN_USAGE_FIELDS = (
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens",
)
MAX_BLIND_RECEIPT_BYTES = 16 * 1024
MAX_BLIND_MAPPING_BYTES = 32 * 1024
MAX_BLIND_ARTIFACT_BYTES = 512 * 1024
MAX_BLIND_FILE_BYTES = 64 * 1024
MAX_BLIND_ANSWER_BYTES = 8 * 1024
MAX_PILOT_DIAGNOSTICS_BYTES = 16 * 1024
MAX_PILOT_METRIC_BYTES = 64 * 1024
QUALITY_FILE_ALLOWLIST = {
    "P01": ("tinytext/text.py", "tests/test_text.py"),
    "P09": ("tinytext/text.py", "tests/test_text.py"),
    "P03": ("scripts/render_report.sh",),
    "P05": ("README.md",),
    "P07": ("STATUS_API.md",),
    "P08": ("scaffoldpkg/pyproject.toml", "scaffoldpkg/src/scaffoldpkg/__init__.py", "scaffoldpkg/tests/test_smoke.py"),
}
PRIVATE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])/(?:home|Users|root|tmp)/\S+"
    r"|[A-Z]:" + re.escape("\\") + r"Users" + re.escape("\\") + r"\S+",
)
PRIVATE_CONTENT_RE = re.compile(
    r"(?i)(?:authorization\s*:|api[_-]?key\s*[:=]|(?:secret|password|access[_-]?token)\s*[:=]|bearer\s+[A-Za-z0-9._~-]{8,})"
    r"|" + PRIVATE_PATH_RE.pattern,
)
TRACE_RE = re.compile(r"JevCompass advice ID:\s*([a-f0-9]{8})", re.I)
SAFE_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
SOURCE_READ_COMMAND_RE = re.compile(r"\b(?:cat|sed|head|tail|less|nl|grep|rg)\b", re.I)
FIXTURE_SOURCE_RE = re.compile(r"(?:README\.md|pyproject\.toml|API_REQUIREMENTS\.md|tests?/|tinytext/|scripts/)", re.I)
TEST_COMMAND_RE = re.compile(r"\b(?:pytest|unittest|tox|nox|bats)\b", re.I)
EDIT_COMMAND_RE = re.compile(r"\b(?:apply_patch|tee|install|touch)\b|(?:^|\s)(?:>>?|\|\s*tee)\s*\S", re.I)
BLIND_LIMITATION = "Metadata-only receipts cannot establish blinded task correctness."
BLIND_OUTCOME_KEYS = frozenset({
    "focused_unittest_exit", "unittest_invocation_observed", "unittest_completion_observed",
    "ci_unittest_command_exit", "ci_unittest_invocation_observed", "ci_unittest_completion_observed",
    "bash_syntax", "no_unset_output_path_defect",
    "bash_syntax_command_exit", "bash_syntax_invocation_observed", "bash_syntax_completion_observed",
    "install_instruction_coherent", "help_instruction_coherent",
    "help_command_exit", "test_instruction_exit", "contract_indicators",
    "scaffold_files_present", "smoke_unittest_exit", "answer_indicator",
})
PILOT_DIAGNOSTIC_CATEGORIES = frozenset({
    "api-design", "codebase", "codex-setup", "coding", "debugging", "documentation",
    "infrastructure", "operations", "package-docs", "planning",
    "project-setup", "research", "review", "source-review", "testing",
})
PILOT_DIAGNOSTIC_STATUSES = frozenset({
    "not_applicable", "not_run", "setup_failed", "not_invoked",
    "invoked_no_result", "unknown", "local", "jev", "cache", "skip",
    "classification-skip", "role-skip", "low-signal-skip",
    "insufficient-candidates",
})
PILOT_INVOCATION_STATUSES = frozenset({"collab-plan", "collab-unavailable"})
PILOT_ADVICE_STATUSES = frozenset({"local", "jev", "cache"})
PILOT_SKIP_STATUSES = frozenset({
    "skip", "classification-skip", "role-skip", "low-signal-skip",
    "insufficient-candidates",
})


def fixture_digest(root: Path) -> str:
    """Hash fixture-relative names and bytes without retaining file contents."""
    digest = hashlib.sha256()
    for path in sorted(
        p for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    ):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def copy_fixture(source: Path, destination: Path, *, case_id: str | None = None) -> str:
    if not source.is_dir():
        raise FileNotFoundError("synthetic CLI fixture is unavailable")
    if any("expected" in path.name.lower() for path in source.rglob("*")):
        raise ValueError("fixture unexpectedly contains evaluator material")
    shutil.copytree(source, destination)
    if case_id == "P09":
        readme = destination / "README.md"
        content = readme.read_text(encoding="utf-8")
        old = "Run tests with `python -m unittest discover -s tests`."
        if content.count(old) != 1:
            raise ValueError("P09 fixture README test instruction changed")
        readme.write_text(content.replace(old, "See the local CI workflow for required validation."), encoding="utf-8")
        workflow = destination / ".github" / "workflows" / "ci.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("name: fixture-ci\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: python -m unittest discover -s tests -v\n", encoding="utf-8")
    # R01 needs a real, inert local branch; no commit or network operation.
    with tempfile.TemporaryDirectory(prefix="jevcompass-git-template-") as template:
        initialized = subprocess.run(
            ["git", "init", "--quiet", "--initial-branch=fixture-main",
             f"--template={template}", str(destination)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={"PATH": os.environ.get("PATH", ""), "HOME": template,
                 "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
            check=False,
        )
    if initialized.returncode != 0:
        raise RuntimeError("synthetic fixture Git initialization failed")
    return fixture_digest(destination)


def extract_event(event: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Reduce Codex JSON events to type, in-memory message text, and safe tool id."""
    event_type = event.get("type")
    item = event.get("item")
    if not isinstance(item, dict):
        item = event.get("payload")
    if not isinstance(item, dict):
        item = event
    item_type = item.get("type") or item.get("item_type")
    text_value = item.get("text") if isinstance(item.get("text"), str) else None
    if text_value is None and isinstance(item.get("content"), list):
        parts = [
            part["text"] for part in item["content"]
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        text_value = "\n".join(parts) if parts else None
    normalized = str(item_type or "").lower()
    if normalized in {"error", "reasoning"}:
        return None, None, None
    if normalized in {"agent_message", "assistant_message", "message"}:
        return "assistant", text_value, None
    tool_types = {
        "command_execution", "function_call", "tool_call", "mcp_tool_call",
        "collaboration_tool_call", "web_search",
    }
    if normalized in tool_types or (
        event_type in {"item.started", "item.completed"}
        and normalized not in {"", "reasoning", "agent_message", "assistant_message"}
    ):
        raw_tool = item.get("name") or item.get("tool_name") or normalized
        tool = str(raw_tool) if SAFE_TOKEN_RE.fullmatch(str(raw_tool)) else "unknown"
        return "tool", None, tool
    return None, None, None


def _safe_turn_usage(event: dict[str, Any]) -> tuple[str, dict[str, int] | None]:
    """Allowlist bounded numeric counters from one Codex turn.completed event."""
    if "usage" not in event:
        return "unavailable", None
    usage = event["usage"]
    if not isinstance(usage, dict) or set(usage) != set(TURN_USAGE_FIELDS):
        return "invalid", None
    if any(
        not isinstance(usage[field], int) or isinstance(usage[field], bool)
        or usage[field] < 0 or usage[field] > MAX_USAGE_COUNTER
        for field in TURN_USAGE_FIELDS
    ):
        return "invalid", None
    if (usage["cached_input_tokens"] > usage["input_tokens"]
            or usage["reasoning_output_tokens"] > usage["output_tokens"]):
        return "invalid", None
    return "available", {field: usage[field] for field in TURN_USAGE_FIELDS}


def parse_event_stream(
    lines: Iterable[str], *, start_monotonic: float,
    event_times: list[float] | None = None,
    assistant_text_sink: list[str] | None = None,
    known_candidate_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Produce metadata-only event order, timings, and reported catalog IDs."""
    events: list[dict[str, Any]] = []
    first_assistant = None
    first_tool = None
    first_source_read_ms = None
    first_action = None
    reported_candidate_ids: list[str] | None = None
    known_ids = tuple(known_candidate_ids)
    pending_command_checks: dict[str, str] = {}
    turn_usage_status = "unavailable"
    turn_usage: dict[str, int] | None = None
    turn_completion_count = 0
    for order, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        kind, text_value, tool_name = extract_event(event)
        observed = (
            event_times[order - 1]
            if event_times is not None and order <= len(event_times)
            else time.monotonic()
        )
        raw_type = event.get("type")
        if raw_type == "turn.completed":
            turn_completion_count += 1
            if turn_completion_count == 1:
                turn_usage_status, turn_usage = _safe_turn_usage(event)
            else:
                turn_usage_status, turn_usage = "invalid", None
        safe_type = str(raw_type) if isinstance(raw_type, str) and SAFE_TOKEN_RE.fullmatch(raw_type) else "unknown"
        record: dict[str, Any] = {
            "order": order,
            "event_type": safe_type,
            "kind": kind or "other",
            "elapsed_ms": round((observed - start_monotonic) * 1000, 2),
        }
        if kind == "assistant":
            if assistant_text_sink is not None and text_value is not None:
                assistant_text_sink.append(text_value)
            match = TRACE_RE.search(text_value or "")
            record["advice_id_present"] = bool(match)
            if first_assistant is None:
                first_assistant = {
                    "order": order,
                    "elapsed_ms": record["elapsed_ms"],
                    "advice_id": match.group(1) if match else None,
                }
                if first_tool is None:
                    reported_candidate_ids = _reported_catalog_ids(text_value or "", known_ids)
        elif kind == "tool":
            record["tool"] = tool_name or "unknown"
            if first_tool is None:
                first_tool = {"order": order, "elapsed_ms": record["elapsed_ms"], "tool": tool_name or "unknown"}
            command_item = event.get("item")
            if not isinstance(command_item, dict):
                command_item = event.get("payload")
            if not isinstance(command_item, dict):
                command_item = event
            raw_command = command_item.get("command")
            command_text = raw_command if isinstance(raw_command, str) else " ".join(raw_command) if isinstance(raw_command, list) and all(isinstance(part, str) for part in raw_command) else ""
            if first_action is None:
                first_action = {
                    "class": _action_class(command_text, tool_name or "unknown"),
                    "elapsed_ms": record["elapsed_ms"],
                }
            command_check = _command_check_kind(command_text)
            item_id = command_item.get("id")
            safe_item_id = item_id if isinstance(item_id, str) and 0 < len(item_id) <= 128 else None
            if event.get("type") == "item.started" and command_check:
                record["command_check_started"] = command_check
                if safe_item_id:
                    pending_command_checks[safe_item_id] = command_check
            if event.get("type") == "item.completed":
                if not command_check and safe_item_id:
                    command_check = pending_command_checks.get(safe_item_id)
                if safe_item_id:
                    pending_command_checks.pop(safe_item_id, None)
                if command_check:
                    record["command_check_completed"] = command_check
                    command_exit = command_item.get("exit_code")
                    if isinstance(command_exit, int) and not isinstance(command_exit, bool):
                        record["command_check"] = command_check
                        record["exit_code"] = command_exit
            if (first_source_read_ms is None and SOURCE_READ_COMMAND_RE.search(command_text)
                    and FIXTURE_SOURCE_RE.search(command_text)):
                first_source_read_ms = record["elapsed_ms"]
        events.append(record)
    id_before_tool = bool(
        first_assistant and first_assistant["advice_id"]
        and (first_tool is None or first_assistant["order"] < first_tool["order"])
    )
    return {
        "event_count": len(events),
        "events": events,
        "first_assistant": first_assistant,
        "first_tool": first_tool,
        "first_source_read_ms": first_source_read_ms,
        "first_action": first_action,
        "advice_id": first_assistant["advice_id"] if id_before_tool else None,
        "advice_id_before_first_tool": id_before_tool,
        "agent_reported_candidate_ids": reported_candidate_ids if id_before_tool else None,
        "token_usage_status": turn_usage_status,
        "token_usage": turn_usage,
    }


def _action_class(command: str, tool_name: str) -> str:
    """Reduce an in-memory command/tool event to a coarse action class."""
    if SOURCE_READ_COMMAND_RE.search(command) and FIXTURE_SOURCE_RE.search(command):
        return "source_read"
    if TEST_COMMAND_RE.search(command):
        return "test"
    if EDIT_COMMAND_RE.search(command) or re.search(r"\b(?:write|edit|patch|replace)_file\b|file_change", tool_name, re.I):
        return "edit"
    return "other"


def _reported_catalog_ids(text: str, known_ids: Iterable[str]) -> list[str]:
    """Extract only exact known IDs that appeared in the supplied agent text."""
    matches: list[tuple[int, str]] = []
    for candidate_id in set(known_ids):
        if not isinstance(candidate_id, str) or not SAFE_TOKEN_RE.fullmatch(candidate_id):
            continue
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_-]){re.escape(candidate_id)}(?![A-Za-z0-9_-])"
        )
        match = pattern.search(text)
        if match:
            matches.append((match.start(), candidate_id))
    return [candidate_id for _, candidate_id in sorted(matches)[:20]]


@lru_cache(maxsize=1)
def _known_catalog_ids() -> tuple[str, ...]:
    """Load only reviewed IDs from the local catalog; availability is not selection."""
    payload = json.loads((ROOT / "src" / "jevcompass" / "catalog_data.json").read_text(encoding="utf-8"))
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    return tuple(sorted({
        entry["id"] for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        and SAFE_TOKEN_RE.fullmatch(entry["id"])
    }))


def _answer_indicator(case_id: str, text: str | None, fixture: Path) -> bool | None:
    """Check only a few fixture-specific answer tokens; this is not grading."""
    if not text or not text.strip():
        return None
    lowered = text.lower()
    if case_id == "R01":
        return "fixture-main" in lowered
    if case_id == "R02":
        count = sum(path.is_file() for path in fixture.iterdir())
        return bool(re.search(rf"(?<!\d){count}(?!\d)", text))
    if case_id == "R03":
        return "readme.md" in lowered and bool(re.search(r"\b(exists|present|yes|true)\b", lowered))
    if case_id == "R04":
        size = (fixture / "pyproject.toml").stat().st_size
        return bool(re.search(rf"(?<!\d){size}(?!\d)", text))
    if case_id == "R05":
        no_files = not any(path.is_file() and path.suffix == ".py" for path in fixture.iterdir())
        return no_files and bool(re.search(r"\b(no|none|zero|empty)\b", lowered))
    if case_id == "R06":
        readme = (fixture / "README.md").read_text(encoding="utf-8", errors="replace").lower()
        return "timeout" in readme and bool(re.search(r"\b(contains|includes|yes|true)\b", lowered)) and "timeout" in lowered
    return None


def _command_check_kind(command: str) -> str | None:
    lowered = command.lower()
    if re.search(r"\bpython(?:3(?:\.\d+)?)?\s+-m\s+unittest\s+discover\s+-s\s+tests\s+-v\b", lowered):
        return "ci_unittest"
    if "unittest" in lowered and "discover" in lowered and "tests" in lowered:
        return "fixture_tests"
    if re.search(r"\bbash\s+-n\b", lowered) and "render_report.sh" in lowered:
        return "bash_syntax_check"
    if "--help" in lowered and "tinytext" in lowered:
        return "cli_help"
    return None


def _fixture_outcome_checks(
    case_id: str, fixture: Path, assistant_text: str | None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, bool | None]:
    """Use static fixture inspection and command results from the Codex sandbox only."""
    events = events or []
    observed_exits: dict[str, int] = {}
    for event in events:
        check = event.get("command_check")
        exit_code = event.get("exit_code")
        if check in {"fixture_tests", "ci_unittest", "cli_help", "bash_syntax_check"} and isinstance(exit_code, int) and not isinstance(exit_code, bool):
            observed_exits[check] = exit_code
    if case_id == "P09":
        exit_code = observed_exits.get("ci_unittest")
        return {
            "ci_unittest_command_exit": exit_code == 0 if exit_code is not None else None,
            "ci_unittest_invocation_observed": any(
                event.get("command_check_started") == "ci_unittest" for event in events
            ),
            "ci_unittest_completion_observed": any(
                event.get("command_check_completed") == "ci_unittest" for event in events
            ),
        }
    if case_id == "P01":
        exit_code = observed_exits.get("fixture_tests")
        return {
            "focused_unittest_exit": exit_code == 0 if exit_code is not None else None,
            "unittest_invocation_observed": any(
                event.get("command_check_started") == "fixture_tests" for event in events
            ),
            "unittest_completion_observed": any(
                event.get("command_check_completed") == "fixture_tests" for event in events
            ),
        }
    if case_id == "P03":
        script = fixture / "scripts" / "render_report.sh"
        try:
            source = script.read_text(encoding="utf-8", errors="replace")
            syntax = subprocess.run(
                ["bash", "-n", str(script)], cwd=fixture, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                check=False,
            ).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            syntax = None
            source = ""
        syntax_exit = observed_exits.get("bash_syntax_check")
        syntax_observed = {
            "bash_syntax_command_exit": syntax_exit == 0 if syntax_exit is not None else None,
            "bash_syntax_invocation_observed": any(
                event.get("command_check_started") == "bash_syntax_check" for event in events
            ),
            "bash_syntax_completion_observed": any(
                event.get("command_check_completed") == "bash_syntax_check" for event in events
            ),
        }
        guarded = None if syntax is None else (
            bool(re.search(r"\$\{OUTPUT_PATH(?::[-=+?])", source))
            or bool(re.search(r"\[\[\s+-v\s+OUTPUT_PATH\s+\]\]", source))
            or not bool(re.search(r"\$(?:\{OUTPUT_PATH\}|OUTPUT_PATH\b)", source))
        )
        return {"bash_syntax": syntax, "no_unset_output_path_defect": guarded, **syntax_observed}
    if case_id == "P05":
        try:
            readme = (fixture / "README.md").read_text(encoding="utf-8", errors="replace").lower()
            pyproject = (fixture / "pyproject.toml").read_text(encoding="utf-8", errors="replace").lower()
            cli_source = (fixture / "tinytext" / "cli.py").read_text(encoding="utf-8", errors="replace").lower()
            module_source = (fixture / "tinytext" / "__main__.py").read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            return {"install_instruction_coherent": None, "help_instruction_coherent": None,
                    "help_command_exit": None, "test_instruction_exit": None}
        install_matches = bool(re.search(r"(?:python\s+-m\s+)?pip\s+install\s+\.", readme)) and "[project]" in pyproject and "setup.py install" not in readme
        help_matches = "python -m tinytext --help" in readme and "--check" in cli_source and "parse_args" in cli_source and "main()" in module_source
        return {
            "install_instruction_coherent": install_matches,
            "help_instruction_coherent": help_matches,
            "help_command_exit": observed_exits.get("cli_help") == 0 if "cli_help" in observed_exits else None,
            "test_instruction_exit": observed_exits.get("fixture_tests") == 0 if "fixture_tests" in observed_exits else None,
        }
    if case_id == "P08":
        try:
            scaffold_files = all(
                _read_fixture_relative(fixture, relative, MAX_BLIND_FILE_BYTES) is not None
                for relative in QUALITY_FILE_ALLOWLIST["P08"]
            )
        except (OSError, ValueError):
            scaffold_files = False
        exit_code = observed_exits.get("fixture_tests")
        return {
            "scaffold_files_present": scaffold_files,
            "smoke_unittest_exit": exit_code == 0 if exit_code is not None else None,
            "unittest_invocation_observed": any(
                event.get("command_check_started") == "fixture_tests" for event in events
            ),
            "unittest_completion_observed": any(
                event.get("command_check_completed") == "fixture_tests" for event in events
            ),
        }
    if case_id == "P07":
        try:
            authored_contract = _read_fixture_relative(fixture, "STATUS_API.md", MAX_BLIND_FILE_BYTES)
        except (OSError, ValueError):
            return {"contract_indicators": False}
        if authored_contract is None:
            return {"contract_indicators": False}
        try:
            contract = authored_contract.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return {"contract_indicators": False}
        lowered = contract.lower()
        method_route = "post" in lowered and "/status" in lowered
        inputs = "required" in lowered and "optional" in lowered
        outputs = "status" in lowered and bool(re.search(r"\b(result|response|success)\b", lowered))
        errors = bool(re.search(r"\b(invalid|validation|4\d\d)\b", lowered)) and bool(re.search(r"\b(server|5\d\d)\b", lowered))
        return {"contract_indicators": method_route and inputs and outputs and errors}
    return {"answer_indicator": _answer_indicator(case_id, assistant_text, fixture)}


def read_safe_metrics(path: Path) -> list[dict[str, Any]]:
    """Return bounded, allowlisted advisor metric fields for private correlation."""
    safe: list[dict[str, Any]] = []
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PILOT_METRIC_BYTES:
            return safe
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:512]
    except OSError:
        return safe
    allowed_categories = PILOT_DIAGNOSTIC_CATEGORIES | {"none"}
    allowed_statuses = PILOT_ADVICE_STATUSES | PILOT_SKIP_STATUSES | PILOT_INVOCATION_STATUSES
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("event") != "UserPromptSubmit":
            continue
        category = record.get("category")
        status = record.get("status")
        trace = record.get("trace")
        duration = record.get("duration_ms")
        safe.append({
            "event": "UserPromptSubmit",
            "category": category if isinstance(category, str) and category in allowed_categories else "unknown",
            "status": status if isinstance(status, str) and status in allowed_statuses else "unknown",
            "duration_ms": (duration if isinstance(duration, (int, float)) and not isinstance(duration, bool)
                            and 0 <= duration <= MAX_TIMEOUT * 1000 and math.isfinite(duration) else None),
            "trace": trace if isinstance(trace, str) and re.fullmatch(r"[a-f0-9]{8}", trace) else None,
        })
    return safe


def _empty_pilot_diagnostic(status: str) -> dict[str, Any]:
    return {
        "category": None,
        "status": status if status in PILOT_DIAGNOSTIC_STATUSES else "unknown",
        "latency_ms": None,
        "trace_reported_before_first_tool": False,
    }


def _pilot_hook_diagnostic(parsed: dict[str, Any], metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Correlate private safe hook metrics without retaining trace identifiers."""
    invocations = [
        item for item in metrics
        if item.get("status") in PILOT_INVOCATION_STATUSES and item.get("trace")
    ]
    if not invocations:
        return _empty_pilot_diagnostic("not_invoked")
    invocation_trace = invocations[-1]["trace"]
    outcome = next((
        item for item in reversed(metrics)
        if item.get("trace") == invocation_trace
        and item.get("status") not in PILOT_INVOCATION_STATUSES
    ), None)
    if outcome is None:
        return _empty_pilot_diagnostic("invoked_no_result")
    status = outcome.get("status")
    if status not in PILOT_ADVICE_STATUSES | PILOT_SKIP_STATUSES:
        status = "unknown"
    category = outcome.get("category")
    if category not in PILOT_DIAGNOSTIC_CATEGORIES:
        category = None
    latency = outcome.get("duration_ms")
    first_assistant = parsed.get("first_assistant")
    reported = bool(
        parsed.get("advice_id_before_first_tool")
        and isinstance(first_assistant, dict)
        and first_assistant.get("advice_id") == invocation_trace
    )
    return {
        "category": category,
        "status": status,
        "latency_ms": latency,
        "trace_reported_before_first_tool": reported,
    }


def correlate_advice(parsed: dict[str, Any], metrics: list[dict[str, Any]]) -> dict[str, Any]:
    advice_id = parsed.get("advice_id")
    match = next((metric for metric in metrics if advice_id and metric.get("trace") == advice_id
                  and metric.get("status") in PILOT_ADVICE_STATUSES), None)
    return {
        "advice_id_before_first_tool": bool(parsed.get("advice_id_before_first_tool")),
        "metric_correlated": match is not None,
        "metric": match,
    }


def _copy_auth(source: Path, destination: Path) -> bool:
    """Copy auth only into a private temporary profile; never read or report it."""
    if not source.is_file():
        return False
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o600)
    return stat.S_IMODE(destination.stat().st_mode) == 0o600


def _check_installed_release(python: Path, expected_version: str = PUBLISHED_PILOT_VERSION) -> None:
    """Verify an explicitly selected published version in an isolated interpreter."""
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", expected_version):
        raise ValueError("installed version must be a numeric X.Y.Z release")
    if not python.is_absolute() or not python.is_file():
        raise ValueError("installed Python must be an absolute regular file")
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE") if key in os.environ}
    try:
        completed = subprocess.run(
            [str(python), "-I", "-c",
             "from importlib.metadata import version; print(version('jevcompass'))"],
            env=env, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("installed JevCompass version could not be verified") from error
    if completed.returncode != 0 or completed.stdout.strip() != expected_version:
        raise RuntimeError("installed JevCompass version does not match published pilot release")


def _install_treatment_hooks(
    home: Path, isolated_python: Path, installed_python: Path | None = None,
) -> None:
    """Install only the selected advisor's hooks into treatment HOME."""
    if installed_python is not None:
        env = _isolated_environment(
            home=home, isolated_python=isolated_python, installed_python=installed_python,
        )
        completed = subprocess.run(
            [str(installed_python), "-m", "jevcompass", "install"],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=15, check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("installed advisor hook setup failed")
        return
    isolated_python.mkdir(mode=0o700, parents=True, exist_ok=True)
    sitecustomize = (
        "import os\nos.environ.pop('OPENROUTER_API_KEY', None)\n"
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
        "import jevcompass.credentials as _credentials\n"
        "_credentials.resolve_api_key = lambda: ''\n"
        "import jevcompass.decisions as _decisions\n"
        "_decisions.resolve_api_key = lambda: ''\n"
    )
    (isolated_python / "sitecustomize.py").write_text(sitecustomize, encoding="utf-8")
    sys.path.insert(0, str(ROOT / "src"))
    from jevcompass.installer import install

    install(home=home)


def _install_bundled_skills(home: Path, installed_python: Path) -> dict[str, bytes]:
    """Install and read back the published skill pack in one temporary profile."""
    codex_home = home / ".codex"
    env = _isolated_environment(
        home=home, isolated_python=home / "python", installed_python=installed_python,
    )
    try:
        completed = subprocess.run(
            [str(installed_python), "-m", "jevcompass", "skills", "install"],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("bundled skill setup failed") from error
    if completed.returncode != 0:
        raise RuntimeError("bundled skill setup failed")

    contents_by_name: dict[str, bytes] = {}
    skills_root = codex_home / "skills"
    for name in BUNDLED_SKILL_NAMES:
        skill_dir = skills_root / name
        entrypoint = skill_dir / "SKILL.md"
        if (skill_dir.is_symlink() or not skill_dir.is_dir()
                or entrypoint.is_symlink() or not entrypoint.is_file()):
            raise RuntimeError("bundled skill verification failed")
        try:
            content = entrypoint.read_bytes()
        except OSError as error:
            raise RuntimeError("bundled skill verification failed") from error
        contents_by_name[name] = content
    return contents_by_name


def _install_source_bundled_skills(home: Path) -> dict[str, bytes]:
    """Install this checkout's optional skills into one disposable profile."""
    from jevcompass.skill_pack import SKILL_NAMES, install_skills

    skills_root = home / ".codex" / "skills"
    install_skills(root=skills_root)
    contents: dict[str, bytes] = {}
    for name in SKILL_NAMES:
        skill_dir = skills_root / name
        entrypoint = skill_dir / "SKILL.md"
        if (skill_dir.is_symlink() or not skill_dir.is_dir()
                or entrypoint.is_symlink() or not entrypoint.is_file()):
            raise RuntimeError("source bundled skill verification failed")
        contents[name] = entrypoint.read_bytes()
    return contents


def _case_settings(case_id: str) -> dict[str, str]:
    return {
        "sandbox": "read-only" if case_id in READ_ONLY_CASES else "workspace-write",
        "approval_policy": "never",
        "ephemeral": "true",
        "other_hooks": "none",
        "network": "no external services; synthetic fixture only",
    }


def _build_command(
    *, codex: str, model: str, reasoning_effort: str, case_id: str,
    preflight: bool = False,
) -> list[str]:
    settings = _case_settings(case_id)
    prompt = PROMPTS[case_id]
    if preflight and case_id in PREFLIGHT_CASES:
        prompt = f"{prompt}\n\n{PREFLIGHT_INSTRUCTION}"
    return [
        codex, "-a", settings["approval_policy"], "exec", "--json", "--ephemeral",
        "--sandbox", settings["sandbox"], "--skip-git-repo-check",
        "--dangerously-bypass-hook-trust", "--model", model,
        "--config", f"model_reasoning_effort={reasoning_effort}", prompt,
    ]


def _isolated_environment(
    *, home: Path, isolated_python: Path, installed_python: Path | None = None,
    allow_openrouter_key: bool = False,
) -> dict[str, str]:
    """Pass only basic runtime variables; exclude inherited keys and proxies."""
    env = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "NO_COLOR", "TZ")
        if key in os.environ
    }
    codex_home = home / ".codex"
    env.update({
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={home / 'no-session-bus'}",
        "GNOME_KEYRING_CONTROL": str(home / "no-keyring"),
    })
    if installed_python is None:
        env["PYTHONPATH"] = os.pathsep.join([str(isolated_python), str(ROOT / "src")])
    if allow_openrouter_key:
        env["OPENROUTER_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
    return env


def _collect_events(
    process: subprocess.Popen[bytes], *, started: float, timeout: int,
    preserve_on_failure: bool = False,
) -> tuple[list[str], list[float], str | None]:
    """Collect bounded JSONL into memory; never write raw events to a file."""
    if process.stdout is None:
        return [], [], "codex_unavailable"
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = bytearray()
    lines: list[str] = []
    times: list[float] = []
    total = 0
    failure = None
    deadline = started + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "timeout"
                break
            for key, _ in selector.select(min(remaining, 0.25)):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > MAX_EVENT_BYTES:
                    failure = "event_output_limit"
                    break
                pending.extend(chunk)
                while True:
                    newline = pending.find(b"\n")
                    if newline < 0:
                        break
                    lines.append(bytes(pending[:newline]).decode("utf-8", errors="replace"))
                    del pending[:newline + 1]
                    times.append(time.monotonic())
            if failure:
                break
        if pending and failure is None:
            lines.append(bytes(pending).decode("utf-8", errors="replace"))
            times.append(time.monotonic())
    finally:
        selector.close()
        process.stdout.close()
    if failure:
        process.kill()
        process.wait()
        return (lines, times, failure) if preserve_on_failure else ([], [], failure)
    try:
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return (lines, times, "timeout") if preserve_on_failure else ([], [], "timeout")
    return lines, times, None


def _run_arm(
    *, codex: str, model: str, reasoning_effort: str, fixture: Path, home: Path,
    case_id: str, timeout: int, treatment: bool, require_auth: bool,
    preflight: bool = False, installed_python: Path | None = None,
    allow_openrouter_key: bool = False,
) -> dict[str, Any]:
    codex_home = home / ".codex"
    codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    auth_root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    if require_auth:
        try:
            auth_ok = _copy_auth(auth_root / "auth.json", codex_home / "auth.json")
        except OSError:
            auth_ok = False
        if not auth_ok:
            return {"status": "failed", "failure": "auth_unavailable"}
    isolated_python = home / "python"
    if treatment:
        try:
            _install_treatment_hooks(home, isolated_python, installed_python)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            return {"status": "failed", "failure": "hooks_setup_failed"}
    env = _isolated_environment(
        home=home, isolated_python=isolated_python, installed_python=installed_python,
        allow_openrouter_key=allow_openrouter_key,
    )
    if treatment:
        env["JEV_ADVISOR_DIAGNOSTIC"] = "1"
        if installed_python is None and os.environ.get("JEVCOMPASS_ADVICE_STYLE") == "compact":
            env["JEVCOMPASS_ADVICE_STYLE"] = "compact"
    command = _build_command(
        codex=codex, model=model, reasoning_effort=reasoning_effort,
        case_id=case_id, preflight=preflight,
    )
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command, cwd=fixture, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        lines, event_times, failure = _collect_events(process, started=started, timeout=timeout)
    except OSError:
        return {"status": "failed", "failure": "codex_unavailable"}
    observed_wall_time_ms = round((time.monotonic() - started) * 1000, 2)
    total_wall_time_ms = observed_wall_time_ms if observed_wall_time_ms <= MAX_TIMEOUT * 1000 else None
    if failure:
        return {
            "status": "failed", "failure": failure,
            "total_wall_time_ms": total_wall_time_ms,
        }
    assistant_texts: list[str] = []
    parsed = parse_event_stream(
        lines, start_monotonic=started, event_times=event_times,
        assistant_text_sink=assistant_texts, known_candidate_ids=_known_catalog_ids(),
    )
    final_assistant_text = assistant_texts[-1] if assistant_texts else None
    outcome_checks = _fixture_outcome_checks(case_id, fixture, final_assistant_text, parsed["events"])
    result: dict[str, Any] = {
        "status": "completed" if process.returncode == 0 else "failed",
        "exit_code": process.returncode,
        "event_count": parsed["event_count"],
        "events": parsed["events"],
        "first_assistant_ms": parsed["first_assistant"]["elapsed_ms"] if parsed["first_assistant"] else None,
        "first_tool_ms": parsed["first_tool"]["elapsed_ms"] if parsed["first_tool"] else None,
        "first_tool_name": parsed["first_tool"]["tool"] if parsed["first_tool"] else None,
        "first_source_read_ms": parsed["first_source_read_ms"],
        "first_action": parsed["first_action"],
        "first_source_read_assessment": "heuristic: bounded command text matched a fixture source-reading command; not a usefulness score",
        "first_useful_action_ms": None,
        "first_useful_action_assessment": "pending blinded evaluator",
        "total_wall_time_ms": total_wall_time_ms,
        "token_usage_status": parsed["token_usage_status"],
        "token_usage": parsed["token_usage"],
        "outcome_checks": outcome_checks,
        "_blind_final_answer": final_assistant_text,
        "outcome_check_scope": "fixture-specific deterministic checks and simple final-answer indicators; heuristic evidence only, not task acceptance",
        "advice_id_before_first_tool": parsed["advice_id_before_first_tool"],
        "_agent_reported_candidate_ids": parsed["agent_reported_candidate_ids"],
    }
    if treatment:
        result["advice_id"] = parsed["advice_id"]
        metrics = read_safe_metrics(home / ".local" / "state" / "jevcompass" / "advisor.jsonl")
        result["advisor_metrics"] = metrics
        result["advice_metric"] = correlate_advice(parsed, metrics)
        result["_pilot_diagnostic"] = _pilot_hook_diagnostic(parsed, metrics)
    if process.returncode != 0:
        result["failure"] = "codex_nonzero_exit"
    return result


def _private_directory_fd(path: str | Path) -> tuple[int, Path]:
    """Open/create a private directory while refusing symlinks in every component."""
    requested = Path(path)
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    normalized = Path(os.path.abspath(requested))
    if normalized == Path(normalized.anchor):
        raise ValueError("blind receipt directory cannot be the filesystem root")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(normalized.anchor, flags)
    try:
        for component in normalized.parts[1:]:
            try:
                next_fd = os.open(component, flags, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(component, mode=0o700, dir_fd=fd)
                next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        os.fchmod(fd, 0o700)
        return fd, normalized
    except BaseException:
        os.close(fd)
        raise


def _write_new_file_at(directory_fd: int, name: str, payload: bytes, mode: int) -> None:
    """Atomically publish a new file without following links or replacing a target."""
    temporary_name = f".tmp-{secrets.token_hex(16)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    file_fd = os.open(temporary_name, flags, mode, dir_fd=directory_fd)
    try:
        os.fchmod(file_fd, mode)
        view = memoryview(payload)
        while view:
            written = os.write(file_fd, view)
            view = view[written:]
        os.fsync(file_fd)
    except BaseException:
        os.close(file_fd)
        os.unlink(temporary_name, dir_fd=directory_fd)
        raise
    os.close(file_fd)
    try:
        os.link(temporary_name, name, src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd, follow_symlinks=False)
        os.fsync(directory_fd)
    finally:
        os.unlink(temporary_name, dir_fd=directory_fd)


def _validate_quality_artifact(artifact: dict[str, Any], case_id: str) -> dict[str, Any]:
    if not isinstance(artifact, dict) or artifact.get("schema") != "jevcompass-blind-cli-quality-v1" or artifact.get("case_id") != case_id:
        raise ValueError("quality artifact has an unknown schema or case")
    if set(artifact) != {"schema", "case_id", "files"} or not isinstance(artifact["files"], list):
        raise ValueError("quality artifact has unknown or missing fields")
    allowed = set(QUALITY_FILE_ALLOWLIST[case_id])
    seen: set[str] = set()
    files = []
    for item in artifact["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise ValueError("quality artifact file has unknown or missing fields")
        relative, content = item["path"], item["content"]
        if not isinstance(relative, str) or relative not in allowed or relative in seen:
            raise ValueError("quality artifact contains a path outside its fixed allowlist")
        _validate_quality_text(content, MAX_BLIND_FILE_BYTES, "quality artifact file")
        if case_id in {"P07", "P08"} and (PROMPTS[case_id] in content or PREFLIGHT_INSTRUCTION in content):
            raise ValueError("P07 quality artifact file must not include the task prompt or preflight transcript")
        seen.add(relative)
        files.append({"path": relative, "content": content})
    normalized = {"schema": artifact["schema"], "case_id": case_id, "files": files}
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_BLIND_ARTIFACT_BYTES:
        raise ValueError("quality artifact exceeds its total size limit")
    return normalized


def write_blind_receipts(
    cases: dict[str, Any], blind_dir: str | Path,
    advice_reviews: dict[tuple[str, str], list[str] | None] | None = None,
    quality_artifacts: dict[tuple[str, str], dict[str, Any]] | None = None,
    pilot_diagnostics: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write scorer-visible receipts separately from the private mapping and review."""
    directory_fd, directory_path = _private_directory_fd(blind_dir)
    receipts_fd: int | None = None
    quality_fd: int | None = None
    try:
        if os.listdir(directory_fd):
            raise FileExistsError("blind receipt directory must be empty")
        os.mkdir("receipts", mode=0o700, dir_fd=directory_fd)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        receipts_fd = os.open("receipts", directory_flags, dir_fd=directory_fd)
        os.fchmod(receipts_fd, 0o700)
        if quality_artifacts is not None:
            os.mkdir("quality_artifacts", mode=0o700, dir_fd=directory_fd)
            quality_fd = os.open("quality_artifacts", directory_flags, dir_fd=directory_fd)
            os.fchmod(quality_fd, 0o700)
        receipts: list[tuple[str, bytes]] = []
        quality_receipts: list[tuple[str, bytes]] = []
        mapping: list[dict[str, str]] = []
        advice_review: list[dict[str, Any]] = []
        pilot_diagnostic_rows: list[dict[str, Any]] = []
        for case_id, case in cases.items():
            for arm_label, arm in case["arms"].items():
                token = secrets.token_urlsafe(24)
                first_action = arm.get("first_action")
                if isinstance(first_action, dict):
                    action_class = first_action.get("class")
                    action_ms = first_action.get("elapsed_ms")
                    if action_class not in {"source_read", "test", "edit", "other"}:
                        action_class = "other"
                    if (not isinstance(action_ms, (int, float)) or isinstance(action_ms, bool)
                            or not math.isfinite(action_ms) or action_ms < 0
                            or action_ms > MAX_TIMEOUT * 1000):
                        action_ms = None
                else:
                    action_class = None
                    action_ms = None
                raw_outcomes = arm.get("outcome_checks")
                safe_outcomes = (
                    {key: value for key, value in raw_outcomes.items()
                     if key in BLIND_OUTCOME_KEYS and (isinstance(value, bool) or value is None)}
                    if isinstance(raw_outcomes, dict) else {}
                )
                raw_exit_code = arm.get("exit_code")
                safe_exit_code = (
                    raw_exit_code if isinstance(raw_exit_code, int)
                    and not isinstance(raw_exit_code, bool) and -255 <= raw_exit_code <= 255 else None
                )
                usage_status = arm.get("token_usage_status")
                raw_usage = arm.get("token_usage")
                safe_usage = None
                if usage_status == "available":
                    if (isinstance(raw_usage, dict) and set(raw_usage) == set(TURN_USAGE_FIELDS)
                            and all(isinstance(raw_usage[field], int) and not isinstance(raw_usage[field], bool)
                                    and 0 <= raw_usage[field] <= MAX_USAGE_COUNTER for field in TURN_USAGE_FIELDS)
                            and raw_usage["cached_input_tokens"] <= raw_usage["input_tokens"]
                            and raw_usage["reasoning_output_tokens"] <= raw_usage["output_tokens"]):
                        safe_usage = {field: raw_usage[field] for field in TURN_USAGE_FIELDS}
                    else:
                        usage_status = "invalid"
                elif usage_status not in {"unavailable", "invalid"}:
                    usage_status = "unavailable"
                raw_wall_time = arm.get("total_wall_time_ms")
                safe_wall_time = (
                    raw_wall_time if isinstance(raw_wall_time, (int, float))
                    and not isinstance(raw_wall_time, bool) and math.isfinite(raw_wall_time)
                    and 0 <= raw_wall_time <= MAX_TIMEOUT * 1000 else None
                )
                receipt = {
                    "schema": "jevcompass-blind-cli-core-v2",
                    "arm_token": token,
                    "case_id": case_id,
                    "first_action_class": action_class,
                    "first_action_ms": action_ms,
                    "execution_status": arm.get("status") if arm.get("status") in {"completed", "failed", "not_run"} else "unknown",
                    "exit_code": safe_exit_code,
                    "outcome_checks": safe_outcomes,
                    "token_usage_status": usage_status,
                    "token_usage": safe_usage,
                    "total_wall_time_ms": safe_wall_time,
                    "limitation": BLIND_LIMITATION,
                }
                encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                if len(encoded) > MAX_BLIND_RECEIPT_BYTES:
                    raise ValueError("blind evaluator receipt exceeds its size limit")
                receipts.append((f"{token}.json", encoded))
                if quality_artifacts is not None and case_id in QUALITY_FILE_ALLOWLIST:
                    raw_artifact = quality_artifacts.get((case_id, arm_label))
                    if raw_artifact is None:
                        raise ValueError("quality artifacts must cover every eligible arm receipt")
                    artifact = _validate_quality_artifact(raw_artifact, case_id)
                    artifact["arm_token"] = token
                    encoded_artifact = json.dumps(
                        artifact, sort_keys=True, separators=(",", ":"), allow_nan=False,
                    ).encode("utf-8")
                    if len(encoded_artifact) > MAX_BLIND_ARTIFACT_BYTES:
                        raise ValueError("quality artifact exceeds its total size limit")
                    quality_receipts.append((f"{token}.json", encoded_artifact))
                mapping.append({"arm_token": token, "case_id": case_id, "arm": arm_label})
                reported_ids = (advice_reviews or {}).get((case_id, arm_label))
                safe_reported_ids = (
                    [candidate_id for candidate_id in reported_ids[:20]
                     if isinstance(candidate_id, str) and candidate_id in _known_catalog_ids()]
                    if isinstance(reported_ids, list) else None
                )
                advice_review.append({
                    "arm_token": token,
                    "agent_reported_candidate_ids": safe_reported_ids,
                })
                if pilot_diagnostics is not None:
                    diagnostic = pilot_diagnostics.get((case_id, arm_label), {})
                    if not isinstance(diagnostic, dict):
                        diagnostic = {}
                    category = diagnostic.get("category")
                    status = diagnostic.get("status")
                    latency = diagnostic.get("latency_ms")
                    pilot_diagnostic_rows.append({
                        "arm_token": token,
                        "category": (category if isinstance(category, str)
                                     and category in PILOT_DIAGNOSTIC_CATEGORIES else None),
                        "status": (status if isinstance(status, str)
                                   and status in PILOT_DIAGNOSTIC_STATUSES else "unknown"),
                        "latency_ms": (latency if isinstance(latency, (int, float))
                                       and not isinstance(latency, bool) and 0 <= latency <= MAX_TIMEOUT * 1000
                                       and math.isfinite(latency) else None),
                        "trace_reported_before_first_tool": diagnostic.get("trace_reported_before_first_tool") is True,
                    })
        encoded_mapping = json.dumps(
            {
                "schema": "jevcompass-blind-cli-core-map-v1",
                "arms": mapping,
                "advice_review": {
                    "timing": "after blind outcome scoring",
                    "interpretation": "Agent-reported text only; not backend-selection evidence.",
                    "arms": advice_review,
                },
            },
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        if len(encoded_mapping) > MAX_BLIND_MAPPING_BYTES:
            raise ValueError("blind receipt mapping exceeds its size limit")
        encoded_diagnostics: bytes | None = None
        if pilot_diagnostics is not None:
            encoded_diagnostics = json.dumps(
                {"schema": "jevcompass-private-hook-diagnostics-v1", "arms": pilot_diagnostic_rows},
                sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
            if len(encoded_diagnostics) > MAX_PILOT_DIAGNOSTICS_BYTES:
                raise ValueError("private pilot diagnostics exceed their size limit")
        expected_quality_count = (
            sum(len(case["arms"]) for case_id, case in cases.items()
                if case_id in QUALITY_FILE_ALLOWLIST)
            if quality_artifacts is not None else 0
        )
        if len(quality_receipts) != expected_quality_count:
            raise ValueError("quality artifacts must cover every eligible arm receipt")
        for name, encoded in receipts:
            _write_new_file_at(receipts_fd, name, encoded, 0o600)
        if quality_fd is not None:
            for name, encoded in quality_receipts:
                _write_new_file_at(quality_fd, name, encoded, 0o600)
        _write_new_file_at(directory_fd, "mapping.json", encoded_mapping, 0o600)
        if encoded_diagnostics is not None:
            _write_new_file_at(directory_fd, "pilot-diagnostics.json", encoded_diagnostics, 0o600)
        return {
            "directory": str(directory_path / "receipts"),
            "receipt_count": len(receipts),
            "quality_artifact_count": len(quality_receipts),
            "quality_directory": str(directory_path / "quality_artifacts") if quality_artifacts is not None else None,
            "mapping": "mapping.json",
        }
    finally:
        if quality_fd is not None:
            os.close(quality_fd)
        if receipts_fd is not None:
            os.close(receipts_fd)
        os.close(directory_fd)


def _read_fixture_relative(root: Path, relative: str, limit: int) -> bytes | None:
    """Read one allowlisted regular file without following fixture symlinks."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("quality artifact path is not fixture-relative")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(root, flags)
    file_fd: int | None = None
    try:
        for component in parts[:-1]:
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        try:
            file_fd = os.open(
                parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise ValueError("quality artifact path must be a regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_fd, min(8192, limit + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ValueError("quality artifact file exceeds its size limit")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)


def _validate_quality_text(value: str, limit: int, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be UTF-8 text")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be UTF-8 text") from error
    if len(encoded) > limit:
        raise ValueError(f"{label} exceeds its size limit")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        raise ValueError(f"{label} contains unsupported control characters")
    if PRIVATE_CONTENT_RE.search(value):
        raise ValueError(f"{label} appears to contain private data")
    return value


def build_quality_artifact(
    case_id: str, fixture: Path, final_answer: str | None = None,
) -> dict[str, Any]:
    """Create a bounded artifact from fixed synthetic-fixture outputs only."""
    if case_id not in QUALITY_FILE_ALLOWLIST:
        raise ValueError("quality artifacts are limited to explicitly allowlisted cases")
    artifact: dict[str, Any] = {
        "schema": "jevcompass-blind-cli-quality-v1",
        "case_id": case_id,
    }
    files: list[dict[str, str]] = []
    for relative in QUALITY_FILE_ALLOWLIST[case_id]:
        try:
            updated = _read_fixture_relative(fixture, relative, MAX_BLIND_FILE_BYTES)
        except FileNotFoundError:
            updated = None
        if updated is None:
            continue
        try:
            original = _read_fixture_relative(FIXTURE, relative, MAX_BLIND_FILE_BYTES)
        except FileNotFoundError:
            original = None  # Newly authored nested scaffold has no source directory.
        if updated == original:
            continue
        try:
            content = updated.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("quality artifact file must contain UTF-8 text") from error
        _validate_quality_text(content, MAX_BLIND_FILE_BYTES, "quality artifact file")
        if case_id in {"P07", "P08"} and (PROMPTS[case_id] in content or PREFLIGHT_INSTRUCTION in content):
            raise ValueError("P07 quality artifact file must not include the task prompt or preflight transcript")
        files.append({"path": relative, "content": content})
    artifact["files"] = files
    encoded = json.dumps(artifact, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_BLIND_ARTIFACT_BYTES:
        raise ValueError("quality artifact exceeds its total size limit")
    return artifact


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _strict_json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object contains a duplicate field")
        result[key] = value
    return result


def _read_limited_json(path: str | Path, limit: int, label: str) -> tuple[bytes, dict[str, Any]]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file")
    if source.stat().st_size > limit:
        raise ValueError(f"{label} exceeds its size limit")
    raw = source.read_bytes()
    try:
        parsed = json.loads(raw, object_pairs_hook=_strict_json_object_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return raw, parsed


def score_blind_pilot(
    receipts_dir: str | Path, mapping_path: str | Path,
    score_path: str | Path, committed_sha256: str,
) -> dict[str, Any]:
    """Validate a committed human score file, unblind locally, and report study gaps."""
    if not isinstance(committed_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", committed_sha256):
        raise ValueError("committed score SHA-256 must be 64 lowercase hexadecimal characters")
    score_bytes, score_doc = _read_limited_json(score_path, MAX_BLIND_MAPPING_BYTES, "score file")
    actual_sha256 = hashlib.sha256(score_bytes).hexdigest()
    if actual_sha256 != committed_sha256:
        raise ValueError("score file SHA-256 does not match the precommitted digest")
    _, mapping_doc = _read_limited_json(mapping_path, MAX_BLIND_MAPPING_BYTES, "private mapping")
    if set(mapping_doc) != {"schema", "arms", "advice_review"} or mapping_doc.get("schema") != "jevcompass-blind-cli-core-map-v1":
        raise ValueError("private mapping has an unknown schema or fields")
    if not isinstance(mapping_doc["arms"], list) or not mapping_doc["arms"]:
        raise ValueError("private mapping must contain at least one arm")
    mapped: dict[str, dict[str, str]] = {}
    seen_case_arms: set[tuple[str, str]] = set()
    for entry in mapping_doc["arms"]:
        if not isinstance(entry, dict) or set(entry) != {"arm_token", "case_id", "arm"}:
            raise ValueError("private mapping arm has unknown or missing fields")
        token, case_id, arm = entry["arm_token"], entry["case_id"], entry["arm"]
        if not isinstance(token, str) or not SAFE_TOKEN_RE.fullmatch(token):
            raise ValueError("private mapping contains an invalid token")
        if token in mapped or not isinstance(case_id, str) or case_id not in CASE_IDS:
            raise ValueError("private mapping contains a duplicate token or invalid case arm")
        if not isinstance(arm, str) or arm not in {"baseline", "treatment"}:
            raise ValueError("private mapping contains an invalid arm label")
        case_arm = (case_id, arm)
        if case_arm in seen_case_arms:
            raise ValueError("private mapping contains a duplicate case arm")
        mapped[token] = {"case_id": case_id, "arm": arm}
        seen_case_arms.add(case_arm)

    advice_review = mapping_doc["advice_review"]
    if not isinstance(advice_review, dict) or set(advice_review) != {"timing", "interpretation", "arms"}:
        raise ValueError("private mapping advice review has unknown or missing fields")
    if (advice_review["timing"] != "after blind outcome scoring"
            or advice_review["interpretation"] != "Agent-reported text only; not backend-selection evidence."
            or not isinstance(advice_review["arms"], list)):
        raise ValueError("private mapping advice review has an invalid format")
    advice_tokens: set[str] = set()
    for entry in advice_review["arms"]:
        if not isinstance(entry, dict) or set(entry) != {"arm_token", "agent_reported_candidate_ids"}:
            raise ValueError("private mapping advice entry has unknown or missing fields")
        token, candidates = entry["arm_token"], entry["agent_reported_candidate_ids"]
        if token not in mapped or token in advice_tokens:
            raise ValueError("private mapping advice entries do not match arm tokens")
        if candidates is not None and (
            not isinstance(candidates, list) or len(candidates) > 20
            or any(not isinstance(value, str) or not SAFE_TOKEN_RE.fullmatch(value) for value in candidates)
        ):
            raise ValueError("private mapping contains invalid reported candidate IDs")
        advice_tokens.add(token)
    if advice_tokens != set(mapped):
        raise ValueError("private mapping advice token set does not match arms")

    pilot_diagnostic_rows: dict[str, dict[str, Any]] | None = None
    diagnostics_path = Path(mapping_path).parent / "pilot-diagnostics.json"
    if diagnostics_path.exists() or diagnostics_path.is_symlink():
        _, diagnostics_doc = _read_limited_json(
            diagnostics_path, MAX_PILOT_DIAGNOSTICS_BYTES, "private pilot diagnostics",
        )
        if set(diagnostics_doc) != {"schema", "arms"} or diagnostics_doc.get("schema") != "jevcompass-private-hook-diagnostics-v1":
            raise ValueError("private pilot diagnostics have an unknown schema or fields")
        if not isinstance(diagnostics_doc["arms"], list):
            raise ValueError("private pilot diagnostics arms must be a list")
        pilot_diagnostic_rows = {}
        for entry in diagnostics_doc["arms"]:
            expected_fields = {
                "arm_token", "category", "status", "latency_ms",
                "trace_reported_before_first_tool",
            }
            if not isinstance(entry, dict) or set(entry) != expected_fields:
                raise ValueError("private pilot diagnostic row has unknown or missing fields")
            token = entry["arm_token"]
            if not isinstance(token, str) or token not in mapped or token in pilot_diagnostic_rows:
                raise ValueError("private pilot diagnostics contain an unknown or duplicate token")
            category = entry["category"]
            if category is not None and (
                not isinstance(category, str) or category not in PILOT_DIAGNOSTIC_CATEGORIES
            ):
                raise ValueError("private pilot diagnostics contain an unknown category")
            status = entry["status"]
            if not isinstance(status, str) or status not in PILOT_DIAGNOSTIC_STATUSES:
                raise ValueError("private pilot diagnostics contain an unknown status")
            latency = entry["latency_ms"]
            if latency is not None and (
                not isinstance(latency, (int, float)) or isinstance(latency, bool)
                or latency < 0 or latency > MAX_TIMEOUT * 1000 or not math.isfinite(latency)
            ):
                raise ValueError("private pilot diagnostics contain invalid latency")
            if not isinstance(entry["trace_reported_before_first_tool"], bool):
                raise ValueError("private pilot diagnostic trace report flag must be boolean")
            if entry["trace_reported_before_first_tool"] and status not in PILOT_ADVICE_STATUSES:
                raise ValueError("private pilot diagnostic reports a trace without emitted advice")
            pilot_diagnostic_rows[token] = entry
        if set(pilot_diagnostic_rows) != set(mapped):
            raise ValueError("private pilot diagnostic token set does not match mapping")

    receipts_root = Path(receipts_dir)
    if receipts_root.is_symlink() or not receipts_root.is_dir():
        raise ValueError("receipts path must be a non-symlink directory")
    files = list(receipts_root.iterdir())
    if any(path.is_symlink() or not path.is_file() or path.suffix != ".json" for path in files):
        raise ValueError("receipts directory contains a non-receipt entry")
    receipts: dict[str, dict[str, Any]] = {}
    expected_v1_receipt_fields = {
        "schema", "arm_token", "case_id", "first_action_class", "first_action_ms",
        "execution_status", "exit_code", "outcome_checks", "limitation",
    }
    expected_v2_receipt_fields = expected_v1_receipt_fields | {
        "token_usage_status", "token_usage", "total_wall_time_ms",
    }
    for path in files:
        token = path.stem
        if token not in mapped or path.name != f"{token}.json" or token in receipts:
            raise ValueError("receipt token set does not match private mapping")
        _, receipt = _read_limited_json(path, MAX_BLIND_RECEIPT_BYTES, "receipt")
        schema = receipt.get("schema")
        expected_fields = (expected_v1_receipt_fields if schema == "jevcompass-blind-cli-core-v1"
                           else expected_v2_receipt_fields if schema == "jevcompass-blind-cli-core-v2"
                           else None)
        if expected_fields is None or set(receipt) != expected_fields:
            raise ValueError("receipt has an unknown schema or fields")
        link = mapped[token]
        if (receipt["arm_token"] != token or receipt["case_id"] != link["case_id"]
                or receipt["limitation"] != BLIND_LIMITATION):
            raise ValueError("receipt does not match its mapped token and case")
        action_class = receipt["first_action_class"]
        if action_class is not None and (
            not isinstance(action_class, str) or action_class not in {"source_read", "test", "edit", "other"}
        ):
            raise ValueError("receipt has an invalid first action class")
        action_ms = receipt["first_action_ms"]
        if action_ms is not None and (
            not isinstance(action_ms, (int, float)) or isinstance(action_ms, bool)
            or not math.isfinite(action_ms) or action_ms < 0 or action_ms > MAX_TIMEOUT * 1000
        ):
            raise ValueError("receipt has an invalid first action time")
        if not isinstance(receipt["execution_status"], str) or receipt["execution_status"] not in {"completed", "failed", "not_run", "unknown"}:
            raise ValueError("receipt has an invalid execution status")
        exit_code = receipt["exit_code"]
        if exit_code is not None and (
            not isinstance(exit_code, int) or isinstance(exit_code, bool) or not -255 <= exit_code <= 255
        ):
            raise ValueError("receipt has an invalid exit code")
        outcomes = receipt["outcome_checks"]
        if not isinstance(outcomes, dict) or any(
            key not in BLIND_OUTCOME_KEYS or (value is not None and not isinstance(value, bool))
            for key, value in outcomes.items()
        ):
            raise ValueError("receipt has invalid outcome checks")
        if schema == "jevcompass-blind-cli-core-v2":
            usage_status = receipt["token_usage_status"]
            usage = receipt["token_usage"]
            if usage_status not in {"available", "unavailable", "invalid"}:
                raise ValueError("receipt has an invalid token usage status")
            if usage_status == "available":
                if (not isinstance(usage, dict) or set(usage) != set(TURN_USAGE_FIELDS)
                        or any(not isinstance(usage[field], int) or isinstance(usage[field], bool)
                               or usage[field] < 0 or usage[field] > MAX_USAGE_COUNTER
                               for field in TURN_USAGE_FIELDS)
                        or usage["cached_input_tokens"] > usage["input_tokens"]
                        or usage["reasoning_output_tokens"] > usage["output_tokens"]):
                    raise ValueError("receipt has invalid token usage counters")
            elif usage is not None:
                raise ValueError("receipt token usage must be null when unavailable or invalid")
            wall_time = receipt["total_wall_time_ms"]
            if wall_time is not None and (
                not isinstance(wall_time, (int, float)) or isinstance(wall_time, bool)
                or not math.isfinite(wall_time) or wall_time < 0
                or wall_time > MAX_TIMEOUT * 1000
            ):
                raise ValueError("receipt has an invalid total wall time")
        receipts[token] = receipt
    if set(receipts) != set(mapped):
        raise ValueError("receipt token set does not exactly match private mapping")

    if set(score_doc) != {"schema", "scores"} or score_doc.get("schema") != "jevcompass-blind-human-scores-v1":
        raise ValueError("score file has an unknown schema or fields")
    if not isinstance(score_doc["scores"], list):
        raise ValueError("score scores must be a list")
    scores: dict[str, dict[str, Any]] = {}
    expected_score_fields = {
        "arm_token", "first_productive_action_ms", "task_quality",
        "required_checks_preserved", "blocked", "privacy_disclosure",
    }
    for score in score_doc["scores"]:
        if not isinstance(score, dict) or set(score) != expected_score_fields:
            raise ValueError("score row has unknown or missing fields")
        token = score["arm_token"]
        if not isinstance(token, str) or token not in mapped or token in scores:
            raise ValueError("score rows contain an unknown or duplicate token")
        timing = score["first_productive_action_ms"]
        if timing is not None and (
            not isinstance(timing, (int, float)) or isinstance(timing, bool)
            or not math.isfinite(timing) or timing < 0 or timing > MAX_TIMEOUT * 1000
        ):
            raise ValueError("score row has an invalid first productive action time")
        for field in ("task_quality", "required_checks_preserved"):
            if score[field] is not None and not isinstance(score[field], bool):
                raise ValueError(f"score row {field} must be boolean or null")
        for field in ("blocked", "privacy_disclosure"):
            if score[field] is not None and not isinstance(score[field], bool):
                raise ValueError(f"score row {field} must be boolean or null")
        scores[token] = score
    if set(scores) != set(mapped):
        raise ValueError("score token set does not exactly match receipts and mapping")

    paired_deltas: list[float] = []
    eligible_deltas: list[float] = []
    by_case: dict[str, dict[str, float | None]] = {}
    for token, link in mapped.items():
        value = scores[token]["first_productive_action_ms"]
        by_case.setdefault(link["case_id"], {})[link["arm"]] = value
    for case_id, arms in by_case.items():
        baseline, treatment = arms.get("baseline"), arms.get("treatment")
        if isinstance(baseline, (int, float)) and isinstance(treatment, (int, float)):
            delta = float(treatment) - float(baseline)
            paired_deltas.append(delta)
            if case_id.startswith("P"):
                eligible_deltas.append(delta)
    usage_by_case: dict[str, dict[str, dict[str, Any]]] = {}
    usage_status_counts = {"available": 0, "unavailable": 0, "invalid": 0, "legacy_v1": 0}
    wall_time_by_case: dict[str, dict[str, float | None]] = {}
    usage_delta_values: dict[str, list[float]] = {
        field: [] for field in (*TURN_USAGE_FIELDS, "uncached_input_tokens_proxy", "total_tokens_proxy")
    }
    paired_usage_cases = 0
    paired_wall_time_deltas: list[float] = []
    for token, link in mapped.items():
        receipt = receipts[token]
        if receipt["schema"] == "jevcompass-blind-cli-core-v1":
            usage_status_counts["legacy_v1"] += 1
            usage_status = "unavailable"
            usage = None
            wall_time = None
        else:
            usage_status = receipt["token_usage_status"]
            usage = receipt["token_usage"]
            wall_time = receipt["total_wall_time_ms"]
            usage_status_counts[usage_status] += 1
        usage_by_case.setdefault(link["case_id"], {})[link["arm"]] = {
            "status": usage_status, "usage": usage,
        }
        wall_time_by_case.setdefault(link["case_id"], {})[link["arm"]] = wall_time
    for case_id, arms in usage_by_case.items():
        baseline, treatment = arms.get("baseline"), arms.get("treatment")
        if (baseline and treatment and baseline["status"] == "available"
                and treatment["status"] == "available"):
            paired_usage_cases += 1
            for field in TURN_USAGE_FIELDS:
                usage_delta_values[field].append(
                    treatment["usage"][field] - baseline["usage"][field]
                )
            usage_delta_values["uncached_input_tokens_proxy"].append(
                treatment["usage"]["input_tokens"] - treatment["usage"]["cached_input_tokens"]
                - baseline["usage"]["input_tokens"] + baseline["usage"]["cached_input_tokens"]
            )
            usage_delta_values["total_tokens_proxy"].append(
                treatment["usage"]["input_tokens"] + treatment["usage"]["output_tokens"]
                - baseline["usage"]["input_tokens"] - baseline["usage"]["output_tokens"]
            )
        wall_arms = wall_time_by_case[case_id]
        baseline_wall, treatment_wall = wall_arms.get("baseline"), wall_arms.get("treatment")
        if isinstance(baseline_wall, (int, float)) and isinstance(treatment_wall, (int, float)):
            paired_wall_time_deltas.append(float(treatment_wall) - float(baseline_wall))
    token_usage_comparison = {
        "arms_by_status": usage_status_counts,
        "paired_cases": paired_usage_cases,
        "median_treatment_minus_baseline": {
            field: _median(values) for field, values in usage_delta_values.items()
        },
        "interpretation": (
            "Token counters are usage-volume proxies only; total_tokens_proxy is input_tokens plus output_tokens. "
            "No billing-dollar estimate is made."
        ),
    }
    total_wall_time_comparison = {
        "paired_cases": len(paired_wall_time_deltas),
        "median_treatment_minus_baseline_ms": _median(sorted(paired_wall_time_deltas)),
        "measure": "Codex process wall time from process launch through completion; separate from first productive action.",
    }
    task_quality_by_case: dict[str, dict[str, bool | None]] = {}
    for token, link in mapped.items():
        task_quality_by_case.setdefault(link["case_id"], {})[link["arm"]] = scores[token]["task_quality"]
    quality_outcomes = {"treatment_better": 0, "baseline_better": 0, "tie": 0, "unscored": 0}
    for arms in task_quality_by_case.values():
        if set(arms) != {"baseline", "treatment"} or arms["baseline"] is None or arms["treatment"] is None:
            quality_outcomes["unscored"] += 1
        elif arms["baseline"] == arms["treatment"]:
            quality_outcomes["tie"] += 1
        elif arms["treatment"]:
            quality_outcomes["treatment_better"] += 1
        else:
            quality_outcomes["baseline_better"] += 1
    quality_outcomes["paired_rated"] = sum(
        quality_outcomes[key] for key in ("treatment_better", "baseline_better", "tie")
    )
    checks = [score["required_checks_preserved"] for score in scores.values()]
    block_ratings = [score["blocked"] for score in scores.values()]
    privacy_ratings = [score["privacy_disclosure"] for score in scores.values()]
    all_blocks_rated = bool(block_ratings) and all(value is not None for value in block_ratings)
    all_privacy_rated = bool(privacy_ratings) and all(value is not None for value in privacy_ratings)
    hook_diagnostic_summary = None
    if pilot_diagnostic_rows is not None:
        treatment_diagnostics = [
            pilot_diagnostic_rows[token] for token, link in mapped.items()
            if link["arm"] == "treatment"
        ]
        emitted = [item for item in treatment_diagnostics if item["status"] in PILOT_ADVICE_STATUSES]
        categories: dict[str, int] = {}
        for item in treatment_diagnostics:
            if item["category"] is not None:
                categories[item["category"]] = categories.get(item["category"], 0) + 1
        hook_diagnostic_summary = {
            "validated": True,
            "treatment_arms": len(treatment_diagnostics),
            "hook_not_invoked": sum(item["status"] == "not_invoked" for item in treatment_diagnostics),
            "deliberate_skips": sum(item["status"] in PILOT_SKIP_STATUSES for item in treatment_diagnostics),
            "advice_emitted": len(emitted),
            "advice_emitted_with_pretool_trace": sum(
                item["trace_reported_before_first_tool"] for item in emitted
            ),
            "advice_emitted_without_pretool_trace": sum(
                not item["trace_reported_before_first_tool"] for item in emitted
            ),
            "median_hook_latency_ms": _median([
                item["latency_ms"] for item in treatment_diagnostics
                if isinstance(item["latency_ms"], (int, float))
            ]),
            "categories": categories,
        }
    missing_evidence = [
        "Host-wide eligible-event coverage is absent from the receipt and score schemas.",
        "Jev remote-call latency and p95 are absent from the receipt and score schemas.",
        "Routine-case advisor silence/request evidence is absent from the receipt and score schemas.",
        "Desktop prompt and supported-subagent pairs are outside this CLI receipt set.",
    ]
    if pilot_diagnostic_rows is None:
        missing_evidence.append("Correlated advice delivery before first tool is absent from the receipt and score schemas.")
    elif hook_diagnostic_summary["advice_emitted_without_pretool_trace"]:
        missing_evidence.append("At least one emitted advice ID was not reported before the first tool.")
    missing_evidence.append(
        "Recommendation usefulness remains unscored; it requires a separate post-unblind relevance review with advice evidence."
    )
    if quality_outcomes["unscored"]:
        missing_evidence.append("At least one case lacks task-quality ratings for both paired arms.")
    if any(score["first_productive_action_ms"] is None for score in scores.values()):
        missing_evidence.append("At least one arm has no human first-productive-action time.")
    if any(value is None for value in checks):
        missing_evidence.append("At least one arm lacks a required-checks-preserved rating.")
    if sum(case_id.startswith("P") for case_id in by_case) < 4:
        missing_evidence.append("The CLI core has fewer than four eligible prompt pairs.")
    case_ids = {link["case_id"] for link in mapped.values()}
    core_present = len(case_ids.intersection(CORE_CASE_IDS))
    if core_present < len(CORE_CASE_IDS):
        missing_evidence.append(f"Only {core_present} of {len(CORE_CASE_IDS)} CLI core cases are present.")
    if sum(case_id.startswith("R") for case_id in case_ids) < len(ROUTINE_CASES):
        missing_evidence.append("The six routine negative-control cases are incomplete.")
    if any(set(arms) != {"baseline", "treatment"} for arms in by_case.values()):
        missing_evidence.append("At least one case lacks its complete baseline/treatment pair.")
    if len(eligible_deltas) == 0:
        missing_evidence.append("No eligible prompt pair has numeric first-productive-action times in both arms.")
    all_deltas = sorted(paired_deltas)
    eligible_sorted = sorted(eligible_deltas)
    return {
        "status": "incomplete",
        "decision": "not_accepted",
        "score_file_sha256": actual_sha256,
        "commitment_verified": True,
        "blindness_verified": False,
        "blindness_note": "The SHA-256 commitment verifies score-file bytes only; it does not prove that evaluator blinding was maintained.",
        "receipt_arms": len(mapped),
        "cases_present": sorted({link["case_id"] for link in mapped.values()}),
        "task_quality": quality_outcomes,
        "recommendation_usefulness": {
            "status": "unscored",
            "rated": 0,
            "rate": None,
            "required_next_step": "Separate post-unblind relevance assessment using advice evidence.",
        },
        "first_productive_action": {
            "paired_cases": len(paired_deltas),
            "median_treatment_minus_baseline_ms": _median(all_deltas),
            "eligible_paired_cases": len(eligible_deltas),
            "eligible_median_treatment_minus_baseline_ms": _median(eligible_sorted),
            "eligible_treatment_faster": sum(value < 0 for value in eligible_deltas),
        },
        "token_usage": token_usage_comparison,
        "total_wall_time": total_wall_time_comparison,
        "required_checks_preserved": {
            "rated": sum(value is not None for value in checks),
            "preserved": sum(value is True for value in checks),
            "omitted": sum(value is False for value in checks),
        },
        "blocks": sum(value is True for value in block_ratings),
        "block_ratings": {"rated": sum(value is not None for value in block_ratings), "total": len(block_ratings)},
        "privacy_disclosures": sum(value is True for value in privacy_ratings),
        "privacy_ratings": {"rated": sum(value is not None for value in privacy_ratings), "total": len(privacy_ratings)},
        "pilot_hook_diagnostics": hook_diagnostic_summary,
        "gate_summary": {
            "zero_blocks_passed": not any(block_ratings) if all_blocks_rated else None,
            "zero_privacy_disclosures_passed": not any(privacy_ratings) if all_privacy_rated else None,
            "required_checks_preserved_passed": bool(checks) and all(value is True for value in checks),
            "eligible_first_productive_action_improved": _median(eligible_sorted) < 0 if eligible_sorted else None,
            "recommendation_usefulness_assessed": False,
        },
        "missing_evidence": missing_evidence,
        "acceptance_note": "This CLI-only scorer never infers recommendation usefulness from receipts and cannot pass overall acceptance while host-wide coverage, Desktop coverage, and recommendation usefulness evidence is absent.",
    }


def run_pilot(
    *, mode: str, model: str | None, reasoning_effort: str = "medium",
    timeout: int = DEFAULT_TIMEOUT, cases: Iterable[str] = CORE_CASE_IDS,
    codex: str | None = None, rng: Any = None, preflight: bool = False,
    blind_dir: str | Path | None = None, blind_quality_artifacts: bool = False,
    installed_python: Path | None = None, installed_version: str = PUBLISHED_PILOT_VERSION,
    allow_openrouter_key: bool = False, with_bundled_skills: bool = False, source_bundled_skills: bool = False,
) -> dict[str, Any]:
    if timeout < 1 or timeout > MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    case_list = list(cases)
    if not case_list or any(case not in CASE_IDS for case in case_list) or len(set(case_list)) != len(case_list):
        raise ValueError("cases must be a non-empty unique subset of supported case IDs")
    if mode not in {"mock", "dry-run", "run"}:
        raise ValueError("mode must be mock, dry-run, or run")
    if mode == "run" and not model:
        raise ValueError("an explicit Codex model is required")
    if blind_quality_artifacts and blind_dir is None:
        raise ValueError("blind quality artifacts require --blind-dir")
    if allow_openrouter_key and (mode != "run" or installed_python is None):
        raise ValueError("OpenRouter key forwarding requires a live installed-release pair")
    if with_bundled_skills and (mode not in {"run", "dry-run"} or installed_python is None):
        raise ValueError("bundled skills require an installed-release run or dry run")
    if source_bundled_skills and (mode not in {"run", "dry-run"} or installed_python is not None or with_bundled_skills):
        raise ValueError("source bundled skills require a source run or dry run")
    if allow_openrouter_key and not os.environ.get("OPENROUTER_API_KEY"):
        raise ValueError("OpenRouter API key is unavailable")
    if installed_python is not None:
        _check_installed_release(installed_python, installed_version)
    elif installed_version != PUBLISHED_PILOT_VERSION:
        raise ValueError("installed version requires an installed Python interpreter")
    if reasoning_effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("reasoning effort must be low, medium, high, or xhigh")
    if not FIXTURE.is_dir():
        raise FileNotFoundError("synthetic CLI fixture is unavailable")
    executable = codex or (shutil.which("codex") if mode == "run" else None)
    if mode == "run" and not executable:
        raise RuntimeError("Codex CLI is unavailable")
    auth_root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    if mode == "run" and not (auth_root / "auth.json").is_file():
        raise RuntimeError("Codex auth.json is unavailable")
    rng = rng or random.SystemRandom()
    rng.shuffle(case_list)
    cases_summary: dict[str, Any] = {}
    advice_reviews: dict[tuple[str, str], list[str] | None] = {}
    quality_artifacts: dict[tuple[str, str], dict[str, Any]] = {}
    pilot_diagnostics: dict[tuple[str, str], dict[str, Any]] = {}
    for case_id in case_list:
        arm_order = ["baseline", "treatment"]
        rng.shuffle(arm_order)
        with tempfile.TemporaryDirectory(prefix="jevcompass-cli-core-") as temporary:
            workspace = Path(temporary)
            arms: dict[str, tuple[Path, Path]] = {}
            digests = []
            for label in ("baseline", "treatment"):
                home = workspace / f"{label}-home"
                home.mkdir(mode=0o700)
                fixture_copy = workspace / f"{label}-fixture"
                digests.append(copy_fixture(FIXTURE, fixture_copy, case_id=case_id))
                arms[label] = (home, fixture_copy)
            if digests[0] != digests[1]:
                raise RuntimeError("paired fixture copies differ")
            skill_metadata = None
            if with_bundled_skills or source_bundled_skills:
                if with_bundled_skills:
                    assert installed_python is not None
                    baseline_skills = _install_bundled_skills(arms["baseline"][0], installed_python)
                    treatment_skills = _install_bundled_skills(arms["treatment"][0], installed_python)
                else:
                    baseline_skills = _install_source_bundled_skills(arms["baseline"][0])
                    treatment_skills = _install_source_bundled_skills(arms["treatment"][0])
                if baseline_skills != treatment_skills:
                    raise RuntimeError("paired bundled skill contents differ")
                skill_metadata = {
                    "installed": True,
                    "skill_count": len(baseline_skills),
                    "content_sha256": {
                        name: hashlib.sha256(content).hexdigest()
                        for name, content in baseline_skills.items()
                    },
                }
            result_arms: dict[str, Any] = {}
            for label in arm_order:
                treatment = label == "treatment"
                if mode == "dry-run":
                    arm_result = {
                        "status": "not_run", "model_called": False,
                        "hooks_would_be_configured": treatment,
                    }
                elif mode == "mock":
                    assert executable
                    home, fixture_copy = arms[label]
                    arm_result = _run_arm(
                        codex=executable, model=model or "synthetic-model",
                        reasoning_effort=reasoning_effort, fixture=fixture_copy,
                        home=home, case_id=case_id, timeout=timeout,
                        treatment=treatment, require_auth=False, preflight=preflight,
                        installed_python=installed_python, allow_openrouter_key=allow_openrouter_key,
                    )
                else:
                    assert executable and model
                    home, fixture_copy = arms[label]
                    arm_result = _run_arm(
                        codex=executable, model=model,
                        reasoning_effort=reasoning_effort, fixture=fixture_copy,
                        home=home, case_id=case_id, timeout=timeout,
                        treatment=treatment, require_auth=True, preflight=preflight,
                        installed_python=installed_python, allow_openrouter_key=allow_openrouter_key,
                    )
                advice_reviews[(case_id, label)] = arm_result.pop("_agent_reported_candidate_ids", None)
                diagnostic = arm_result.pop("_pilot_diagnostic", None)
                if diagnostic is None:
                    if not treatment:
                        diagnostic = _empty_pilot_diagnostic("not_applicable")
                    elif mode == "dry-run":
                        diagnostic = _empty_pilot_diagnostic("not_run")
                    elif arm_result.get("failure") == "hooks_setup_failed":
                        diagnostic = _empty_pilot_diagnostic("setup_failed")
                    elif arm_result.get("failure") == "auth_unavailable":
                        diagnostic = _empty_pilot_diagnostic("not_run")
                    else:
                        diagnostic = _empty_pilot_diagnostic("not_invoked")
                pilot_diagnostics[(case_id, label)] = diagnostic
                final_answer = arm_result.pop("_blind_final_answer", None)
                if blind_quality_artifacts and case_id in QUALITY_FILE_ALLOWLIST:
                    quality_artifacts[(case_id, label)] = build_quality_artifact(
                        case_id, arms[label][1], final_answer,
                    )
                result_arms[label] = arm_result
            case_summary = {
                "arm_order": arm_order,
                "source_sha256": digests[0],
                "fixture_copies_identical": True,
                "sandbox": _case_settings(case_id)["sandbox"],
                "routine_negative_control": case_id in ROUTINE_CASES,
                "arms": result_arms,
            }
            if skill_metadata is not None:
                case_summary["bundled_skills"] = skill_metadata
            cases_summary[case_id] = case_summary
    failed = any(
        arm.get("status") == "failed"
        for case in cases_summary.values()
        for arm in case["arms"].values()
    )
    result = {
        "pilot": "jevcompass-cli-core",
        "status": "failed" if failed else "completed",
        "mode": mode,
        "preflight": bool(preflight),
        "case_order": case_list,
        "model": model if mode == "run" else None,
        "reasoning_effort": reasoning_effort,
        "timeout_seconds_per_arm": timeout,
        "advisor_source": "installed-distribution" if installed_python else "source-checkout",
        "advisor_version": installed_version if installed_python else None,
        "openrouter_key_forwarded": bool(allow_openrouter_key),
        "other_hooks": "none",
        "receipt_scope": "safe metadata only; no prompt, source, transcript, command text, or auth",
        "scoring_note": "First useful action requires blinded evaluator review; source-read timing is a command-text heuristic only.",
        "task_outcome_note": "Arm exit status records execution only. Fixture outcome checks are limited deterministic or keyword heuristics and do not establish overall task correctness or acceptance.",
        "cases": cases_summary,
    }
    if blind_dir is not None:
        receipt_summary = write_blind_receipts(
            cases_summary, blind_dir, advice_reviews,
            quality_artifacts if blind_quality_artifacts else None,
            pilot_diagnostics,
        )
        result.pop("cases", None)
        result["blind_receipts"] = {
            "receipt_count": receipt_summary["receipt_count"],
            "mapping_file": receipt_summary["mapping"],
            "pilot_diagnostics_file": "pilot-diagnostics.json" if pilot_diagnostics is not None else None,
            "limitation": BLIND_LIMITATION,
        }
        if blind_quality_artifacts:
            result["blind_receipts"]["quality_artifact_count"] = receipt_summary["quality_artifact_count"]
            result["blind_receipts"]["quality_artifacts_dir"] = "quality_artifacts"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate fixture and pairing without launching Codex")
    mode.add_argument("--mock", action="store_true", help="run against an offline fake Codex executable")
    parser.add_argument("--model", help="same explicit Codex model for both arms (required for live run)")
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh"), default="medium")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"per-arm timeout, maximum {MAX_TIMEOUT}s")
    parser.add_argument("--preflight", action="store_true", help="ask both arms to report pre-tool JevCompass advice evidence")
    parser.add_argument("--blind-dir", type=Path, help="write private, opaque per-arm evaluator receipts and separate mapping")
    parser.add_argument("--installed-python", type=Path,
                        help="absolute interpreter path of a published JevCompass release")
    parser.add_argument("--installed-version", default=PUBLISHED_PILOT_VERSION,
                        help="exact published version expected in --installed-python (default: 0.1.16)")
    parser.add_argument("--with-bundled-skills", action="store_true",
                        help="install and verify the published bundled skills in both temporary profiles (live or dry run)")
    parser.add_argument("--source-bundled-skills", action="store_true",
                        help="install and verify this checkout's optional skills in both source-mode profiles")
    parser.add_argument("--allow-openrouter-key", action="store_true",
                        help="opt in to equal OpenRouter key presence in both live installed-release arms")
    parser.add_argument(
        "--blind-quality-artifacts", action="store_true",
        help="also write bounded, opaque fixture-output artifacts for P01/P03/P05/P07/P08/P09",
    )
    parser.add_argument("--score-receipts", type=Path, help="score an existing blind receipt directory")
    parser.add_argument("--score-mapping", type=Path, help="private arm mapping for offline blind scoring")
    parser.add_argument("--score-file", type=Path, help="blind human score JSON with task_quality bool/null for each opaque arm token")
    parser.add_argument("--score-sha256", help="precommitted SHA-256 of the exact human score file bytes")
    parser.add_argument("--cases", nargs="+", choices=CASE_IDS, default=list(CORE_CASE_IDS))
    args = parser.parse_args(argv)
    score_args = (args.score_receipts, args.score_mapping, args.score_file, args.score_sha256)
    if any(value is not None for value in score_args):
        if not all(value is not None for value in score_args):
            parser.error("--score-receipts, --score-mapping, --score-file, and --score-sha256 are required together")
        try:
            result = score_blind_pilot(
                args.score_receipts, args.score_mapping, args.score_file, args.score_sha256,
            )
        except (OSError, ValueError) as error:
            print(json.dumps({"pilot": "jevcompass-cli-core-scoring", "status": "failed", "failure": str(error)}))
            return 1
        print(json.dumps(result, sort_keys=True))
        return 0
    selected_mode = "dry-run" if args.dry_run else "mock" if args.mock else "run"
    if args.blind_quality_artifacts and args.blind_dir is None:
        parser.error("--blind-quality-artifacts requires --blind-dir")
    if selected_mode == "run" and not args.model:
        parser.error("--model is required for a live pair")
    try:
        result = run_pilot(
            mode=selected_mode, model=args.model,
            reasoning_effort=args.reasoning_effort,
            timeout=args.timeout, cases=args.cases,
            preflight=args.preflight, blind_dir=args.blind_dir,
            blind_quality_artifacts=args.blind_quality_artifacts,
            installed_python=args.installed_python,
            installed_version=args.installed_version,
            allow_openrouter_key=args.allow_openrouter_key,
            with_bundled_skills=args.with_bundled_skills,
            source_bundled_skills=args.source_bundled_skills,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"pilot": "jevcompass-cli-core", "status": "failed", "failure": str(error)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
