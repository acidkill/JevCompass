#!/usr/bin/env python3
"""Run bounded, synthetic-only CLI pairs for JevCompass core cases.

Live mode invokes the same explicit Codex model/settings in randomized
baseline/treatment arms. Use --mock for offline fake-CLI validation.
Receipts contain allowlisted metadata only; no prompt, transcript, source,
command text, authentication material, or model response is retained.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
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
CASE_IDS = ("P01", "P03", "P05", "P07", "R01", "R02", "R03", "R04", "R05", "R06")
PROMPTS = {
    "P01": "Implement a small Python helper that normalizes whitespace in a string, and add focused tests for empty input and repeated spaces.",
    "P03": "Fix the Bash script's unset-variable defect and run a syntax check on the edited script.",
    "P05": "Update the Python project README's install instructions to match the current CLI help and existing test behavior.",
    "P07": "Design an API contract for a Python endpoint that accepts a request and returns a validated status result; include input and error cases.",
    "R01": "Print the current branch name in the synthetic fixture.",
    "R02": "Count the top-level files in the synthetic fixture.",
    "R03": "Check whether README.md exists in the synthetic fixture.",
    "R04": "Show the size of pyproject.toml in bytes in the synthetic fixture.",
    "R05": "List the top-level Python files in the synthetic fixture.",
    "R06": "Check whether README.md contains the word timeout.",
}
READ_ONLY_CASES = frozenset({"P07", "R01", "R02", "R03", "R04", "R05", "R06"})
ROUTINE_CASES = frozenset({"R01", "R02", "R03", "R04", "R05", "R06"})
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 300
MAX_EVENT_BYTES = 4 * 1024 * 1024
TRACE_RE = re.compile(r"JevCompass advice ID:\s*([a-f0-9]{8})", re.I)
SAFE_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
SOURCE_READ_COMMAND_RE = re.compile(r"\b(?:cat|sed|head|tail|less|nl|grep|rg)\b", re.I)
FIXTURE_SOURCE_RE = re.compile(r"(?:README\.md|pyproject\.toml|API_REQUIREMENTS\.md|tests?/|tinytext/|scripts/)", re.I)


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


def copy_fixture(source: Path, destination: Path) -> str:
    if not source.is_dir():
        raise FileNotFoundError("synthetic CLI fixture is unavailable")
    if any("expected" in path.name.lower() for path in source.rglob("*")):
        raise ValueError("fixture unexpectedly contains evaluator material")
    shutil.copytree(source, destination)
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


def parse_event_stream(
    lines: Iterable[str], *, start_monotonic: float,
    event_times: list[float] | None = None,
    assistant_text_sink: list[str] | None = None,
) -> dict[str, Any]:
    """Produce metadata-only event order, timings, and pre-tool advice evidence."""
    events: list[dict[str, Any]] = []
    first_assistant = None
    first_tool = None
    first_source_read_ms = None
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
            if event.get("type") == "item.completed":
                command_check = _command_check_kind(command_text)
                command_exit = command_item.get("exit_code")
                if command_check and isinstance(command_exit, int) and not isinstance(command_exit, bool):
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
        "advice_id": first_assistant["advice_id"] if id_before_tool else None,
        "advice_id_before_first_tool": id_before_tool,
    }


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
    if "unittest" in lowered and "discover" in lowered and "tests" in lowered:
        return "fixture_tests"
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
        if check in {"fixture_tests", "cli_help"} and isinstance(exit_code, int) and not isinstance(exit_code, bool):
            observed_exits[check] = exit_code
    if case_id == "P01":
        exit_code = observed_exits.get("fixture_tests")
        return {"focused_unittest_exit": exit_code == 0 if exit_code is not None else None}
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
            return {"bash_syntax": None, "no_unset_output_path_defect": None}
        guarded = bool(re.search(r"\$\{OUTPUT_PATH(?::[-=+?])", source)) or bool(
            re.search(r"\[\[\s+-v\s+OUTPUT_PATH\s+\]\]", source)
        ) or not bool(re.search(r"\$(?:\{OUTPUT_PATH\}|OUTPUT_PATH\b)", source))
        return {"bash_syntax": syntax, "no_unset_output_path_defect": guarded}
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
    if case_id == "P07":
        if not assistant_text or not assistant_text.strip():
            return {"contract_indicators": None}
        lowered = assistant_text.lower()
        method_route = "post" in lowered and "/status" in lowered
        inputs = "required" in lowered and "optional" in lowered
        outputs = "status" in lowered and bool(re.search(r"\b(result|response|success)\b", lowered))
        errors = bool(re.search(r"\b(invalid|validation|4\d\d)\b", lowered)) and bool(re.search(r"\b(server|5\d\d)\b", lowered))
        return {"contract_indicators": method_route and inputs and outputs and errors}
    return {"answer_indicator": _answer_indicator(case_id, assistant_text, fixture)}


def read_safe_metrics(path: Path) -> list[dict[str, Any]]:
    """Return only allowlisted advisor metric fields."""
    safe: list[dict[str, Any]] = []
    if not path.is_file():
        return safe
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("event") != "UserPromptSubmit":
            continue
        event = record.get("event")
        category = record.get("category")
        status = record.get("status")
        trace = record.get("trace")
        duration = record.get("duration_ms")
        safe.append({
            "event": event if event == "UserPromptSubmit" else "unknown",
            "category": category if isinstance(category, str) and SAFE_TOKEN_RE.fullmatch(category) else "unknown",
            "status": status if isinstance(status, str) and SAFE_TOKEN_RE.fullmatch(status) else "unknown",
            "duration_ms": duration if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None,
            "trace": trace if isinstance(trace, str) and re.fullmatch(r"[a-f0-9]{8}", trace) else None,
        })
    return safe


def correlate_advice(parsed: dict[str, Any], metrics: list[dict[str, Any]]) -> dict[str, Any]:
    advice_id = parsed.get("advice_id")
    match = next((metric for metric in metrics if advice_id and metric.get("trace") == advice_id
                  and metric.get("status") in {"local", "jev", "cache"}), None)
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


def _install_treatment_hooks(home: Path, isolated_python: Path) -> None:
    """Install only this checkout's prompt-advisor hooks into treatment HOME."""
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
) -> list[str]:
    settings = _case_settings(case_id)
    return [
        codex, "-a", settings["approval_policy"], "exec", "--json", "--ephemeral",
        "--sandbox", settings["sandbox"], "--skip-git-repo-check",
        "--dangerously-bypass-hook-trust", "--model", model,
        "--config", f"model_reasoning_effort={reasoning_effort}", PROMPTS[case_id],
    ]


def _isolated_environment(*, home: Path, isolated_python: Path) -> dict[str, str]:
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
        "PYTHONPATH": os.pathsep.join([str(isolated_python), str(ROOT / "src")]),
    })
    return env


def _collect_events(
    process: subprocess.Popen[bytes], *, started: float, timeout: int,
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
        return [], [], failure
    try:
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return [], [], "timeout"
    return lines, times, None


def _run_arm(
    *, codex: str, model: str, reasoning_effort: str, fixture: Path, home: Path,
    case_id: str, timeout: int, treatment: bool, require_auth: bool,
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
            _install_treatment_hooks(home, isolated_python)
        except (OSError, RuntimeError, ValueError):
            return {"status": "failed", "failure": "hooks_setup_failed"}
    settings = _case_settings(case_id)
    env = _isolated_environment(home=home, isolated_python=isolated_python)
    command = _build_command(
        codex=codex, model=model, reasoning_effort=reasoning_effort,
        case_id=case_id,
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
    if failure:
        return {"status": "failed", "failure": failure}
    assistant_texts: list[str] = []
    parsed = parse_event_stream(
        lines, start_monotonic=started, event_times=event_times,
        assistant_text_sink=assistant_texts,
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
        "first_source_read_assessment": "heuristic: bounded command text matched a fixture source-reading command; not a usefulness score",
        "first_useful_action_ms": None,
        "first_useful_action_assessment": "pending blinded evaluator",
        "outcome_checks": outcome_checks,
        "outcome_check_scope": "fixture-specific deterministic checks and simple final-answer indicators; heuristic evidence only, not task acceptance",
        "advice_id_before_first_tool": parsed["advice_id_before_first_tool"],
    }
    if treatment:
        result["advice_id"] = parsed["advice_id"]
        metrics = read_safe_metrics(home / ".local" / "state" / "jevcompass" / "advisor.jsonl")
        result["advisor_metrics"] = metrics
        result["advice_metric"] = correlate_advice(parsed, metrics)
    if process.returncode != 0:
        result["failure"] = "codex_nonzero_exit"
    return result


def run_pilot(
    *, mode: str, model: str | None, reasoning_effort: str = "medium",
    timeout: int = DEFAULT_TIMEOUT, cases: Iterable[str] = CASE_IDS,
    codex: str | None = None, rng: Any = None,
) -> dict[str, Any]:
    if timeout < 1 or timeout > MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    case_list = list(cases)
    if not case_list or any(case not in CASE_IDS for case in case_list) or len(set(case_list)) != len(case_list):
        raise ValueError("cases must be a non-empty unique subset of P01/P03/P05/P07/R01-R06")
    if mode not in {"mock", "dry-run", "run"}:
        raise ValueError("mode must be mock, dry-run, or run")
    if mode == "run" and not model:
        raise ValueError("an explicit Codex model is required")
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
                digests.append(copy_fixture(FIXTURE, fixture_copy))
                arms[label] = (home, fixture_copy)
            if digests[0] != digests[1]:
                raise RuntimeError("paired fixture copies differ")
            result_arms: dict[str, Any] = {}
            for label in arm_order:
                treatment = label == "treatment"
                if mode == "dry-run":
                    result_arms[label] = {
                        "status": "not_run", "model_called": False,
                        "hooks_would_be_configured": treatment,
                    }
                elif mode == "mock":
                    assert executable
                    home, fixture_copy = arms[label]
                    result_arms[label] = _run_arm(
                        codex=executable, model=model or "synthetic-model",
                        reasoning_effort=reasoning_effort, fixture=fixture_copy,
                        home=home, case_id=case_id, timeout=timeout,
                        treatment=treatment, require_auth=False,
                    )
                else:
                    assert executable and model
                    home, fixture_copy = arms[label]
                    result_arms[label] = _run_arm(
                        codex=executable, model=model,
                        reasoning_effort=reasoning_effort, fixture=fixture_copy,
                        home=home, case_id=case_id, timeout=timeout,
                        treatment=treatment, require_auth=True,
                    )
            cases_summary[case_id] = {
                "arm_order": arm_order,
                "source_sha256": digests[0],
                "fixture_copies_identical": True,
                "sandbox": _case_settings(case_id)["sandbox"],
                "routine_negative_control": case_id in ROUTINE_CASES,
                "arms": result_arms,
            }
    failed = any(
        arm.get("status") == "failed"
        for case in cases_summary.values()
        for arm in case["arms"].values()
    )
    return {
        "pilot": "jevcompass-cli-core",
        "status": "failed" if failed else "completed",
        "mode": mode,
        "case_order": case_list,
        "model": model if mode == "run" else None,
        "reasoning_effort": reasoning_effort,
        "timeout_seconds_per_arm": timeout,
        "openrouter_key_forwarded": False,
        "other_hooks": "none",
        "receipt_scope": "safe metadata only; no prompt, source, transcript, command text, or auth",
        "scoring_note": "First useful action requires blinded evaluator review; source-read timing is a command-text heuristic only.",
        "task_outcome_note": "Arm exit status records execution only. Fixture outcome checks are limited deterministic or keyword heuristics and do not establish overall task correctness or acceptance.",
        "cases": cases_summary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate fixture and pairing without launching Codex")
    mode.add_argument("--mock", action="store_true", help="run against an offline fake Codex executable")
    parser.add_argument("--model", help="same explicit Codex model for both arms (required for live run)")
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh"), default="medium")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"per-arm timeout, maximum {MAX_TIMEOUT}s")
    parser.add_argument("--cases", nargs="+", choices=CASE_IDS, default=list(CASE_IDS))
    args = parser.parse_args(argv)
    selected_mode = "dry-run" if args.dry_run else "mock" if args.mock else "run"
    if selected_mode == "run" and not args.model:
        parser.error("--model is required for a live pair")
    try:
        result = run_pilot(
            mode=selected_mode, model=args.model,
            reasoning_effort=args.reasoning_effort,
            timeout=args.timeout, cases=args.cases,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(json.dumps({"pilot": "jevcompass-cli-core", "status": "failed", "failure": str(error)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
