#!/usr/bin/env python3
"""Bounded, synthetic-only W02 paired CLI runner.

Normal mode launches two authenticated Codex CLI runs. Use --dry-run or --mock
for local validation without launching Codex or contacting a model.
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
FIXTURE = ROOT / "tests" / "fixtures" / "workflow_review"
PROMPT = (
    "Review the synthetic proposal draft against the repository map and "
    "canonical pricing table. Flag missing facts, keep the draft unsent, "
    "and report its intended path and filename."
)
DEFAULT_TIMEOUT = 90
MAX_TIMEOUT = 300
MAX_EVENT_BYTES = 4 * 1024 * 1024
TRACE_RE = re.compile(r"JevCompass advice ID:\s*([a-f0-9]{8})", re.I)
EXPECTED_PATH = (
    "clients/fjordly-labs/drafts/"
    "Proposal_Fjordly_Labs_Data_Migration_2026-10-15.md"
)


def fixture_digest(root: Path) -> str:
    """Hash relative names and file bytes without including filesystem paths."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def copy_fixture(source: Path, destination: Path) -> str:
    if not source.is_dir():
        raise FileNotFoundError("synthetic W02 fixture is unavailable")
    if any("expected" in p.name.lower() for p in source.rglob("*")):
        raise ValueError("fixture unexpectedly contains evaluator material")
    shutil.copytree(source, destination)
    return fixture_digest(destination)


def extract_event(event: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Return event kind, assistant text, and tool name from Codex JSON."""
    event_type = event.get("type")
    item = event.get("item")
    if not isinstance(item, dict):
        item = event.get("payload")
    if not isinstance(item, dict):
        item = event
    item_type = item.get("type") or item.get("item_type")
    text_value: str | None = None
    if isinstance(item.get("text"), str):
        text_value = item["text"]
    elif isinstance(item.get("content"), list):
        parts = [
            part.get("text", "")
            for part in item["content"]
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        text_value = "\n".join(parts) if parts else None

    normalized = str(item_type or "").lower()
    if normalized in {"error", "reasoning"}:
        return None, None, None
    if normalized in {"agent_message", "assistant_message", "message"}:
        return "assistant", text_value, None
    if normalized in {
        "command_execution", "function_call", "tool_call", "mcp_tool_call",
        "collaboration_tool_call", "web_search",
    }:
        tool = item.get("name") or item.get("tool_name") or normalized
        safe_tool = str(tool) if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(tool)) else normalized
        return "tool", None, safe_tool
    # Some Codex versions expose tool calls as top-level item.started events.
    if event_type in {"item.started", "item.completed"} and normalized and normalized not in {
        "reasoning", "agent_message", "assistant_message",
    }:
        tool = item.get("name") or item.get("tool_name") or normalized
        safe_tool = str(tool) if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(tool)) else normalized
        return "tool", None, safe_tool
    return None, None, None


def parse_event_stream(
    lines: Iterable[str], *, start_monotonic: float,
    event_times: list[float] | None = None,
) -> dict[str, Any]:
    """Summarize event order/timing and advice visibility without retaining text."""
    events: list[dict[str, Any]] = []
    captured_times = event_times or []
    first_assistant: dict[str, Any] | None = None
    first_tool: dict[str, Any] | None = None
    for order, line in enumerate(lines, 1):
        try:
            obj = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        kind, text_value, tool_name = extract_event(obj)
        observed = captured_times[order - 1] if order <= len(captured_times) else time.monotonic()
        observed_ms = round((observed - start_monotonic) * 1000, 2)
        raw_type = obj.get("type")
        event_type = (
            str(raw_type) if isinstance(raw_type, str)
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", raw_type)
            else "unknown"
        )
        record: dict[str, Any] = {
            "order": order,
            "event_type": event_type,
            "kind": kind or "other",
            "elapsed_ms": observed_ms,
        }
        if kind is None:
            events.append(record)
            continue
        if kind == "assistant":
            trace_match = TRACE_RE.search(text_value or "")
            record["advice_id_present"] = bool(trace_match)
            if first_assistant is None:
                first_assistant = {
                    "order": order,
                    "elapsed_ms": observed_ms,
                    "text": text_value or "",
                    "advice_id": trace_match.group(1) if trace_match else None,
                }
        else:
            record["tool"] = tool_name or "unknown"
            if first_tool is None:
                first_tool = {"order": order, "elapsed_ms": observed_ms}
        events.append(record)

    visible_id = None
    visible_before_tool = False
    if first_assistant:
        visible_id = first_assistant["advice_id"]
        visible_before_tool = bool(
            visible_id and (
                first_tool is None
                or first_assistant["order"] < first_tool["order"]
            )
        )
    return {
        "event_count": len(events),
        "events": events,
        "first_assistant": first_assistant,
        "first_tool": first_tool,
        "advice_id": visible_id if visible_before_tool else None,
        "advice_id_before_first_tool": visible_before_tool,
    }


def read_safe_metrics(path: Path) -> list[dict[str, Any]]:
    """Read only allowlisted metric fields; never return arbitrary log content."""
    safe: list[dict[str, Any]] = []
    if not path.is_file():
        return safe
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        event = record.get("event")
        if event not in {"UserPromptSubmit", "SubagentStart"}:
            continue
        metric = {
            "event": event,
            "category": record.get("category"),
            "status": record.get("status"),
            "duration_ms": record.get("duration_ms"),
            "trace": record.get("trace"),
        }
        safe.append(metric)
    return safe


def correlate_advice(parsed: dict[str, Any], metrics: list[dict[str, Any]]) -> dict[str, Any]:
    advice_id = parsed.get("advice_id")
    match = next(
        (m for m in metrics if advice_id and m.get("trace") == advice_id
         and m.get("status") in {"local", "jev", "cache"}),
        None,
    )
    return {
        "advice_id_before_first_tool": bool(parsed.get("advice_id_before_first_tool")),
        "metric_correlated": match is not None,
        "metric": match,
    }


def score_answer(answer: str) -> dict[str, bool]:
    """Deterministic indicators only; not a blinded expert quality assessment."""
    lower = answer.lower()
    has_price = bool(re.search(r"12[,. ]?500", answer))
    has_canonical_source = "canonical-pricing.md" in lower
    mentions_missing_scope = bool(
        re.search(r"source[- ]system", lower)
        and re.search(r"missing|not provided|not confirmed|confirm|unknown", lower)
    )
    has_destination = EXPECTED_PATH.lower() in lower
    says_unsent = bool(
        re.search(r"unsent|not sent|remain(?:s)? a draft|draft remains", lower)
    )
    return {
        "canonical_price_indicator": has_price and has_canonical_source,
        "missing_source_count_indicator": mentions_missing_scope,
        "intended_path_indicator": has_destination,
        "unsent_draft_indicator": says_unsent,
    }


def _copy_auth(source: Path, destination: Path) -> bool:
    """Copy Codex CLI auth to an isolated profile without reading/logging contents."""
    if not source.is_file():
        return False
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o600)
    return stat.S_IMODE(destination.stat().st_mode) == 0o600


def _install_treatment_hooks(home: Path, isolated_python: Path) -> None:
    """Install this checkout's hooks into the isolated treatment CODEX_HOME."""
    isolated_python.mkdir(mode=0o700, parents=True, exist_ok=True)
    sitecustomize = (
        "import os\n"
        "os.environ.pop('OPENROUTER_API_KEY', None)\n"
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
        "import jevcompass.credentials as _jc_credentials\n"
        "_jc_credentials.resolve_api_key = lambda: ''\n"
        "import jevcompass.decisions as _jc_decisions\n"
        "_jc_decisions.resolve_api_key = lambda: ''\n"
    )
    (isolated_python / "sitecustomize.py").write_text(sitecustomize, encoding="utf-8")
    sys.path.insert(0, str(ROOT / "src"))
    from jevcompass.installer import install

    install(home=home)


def _safe_mock_lines() -> list[str]:
    assistant = {
        "type": "item.completed",
        "item": {
            "type": "agent_message",
            "text": (
                "JevCompass advice ID: abcdef12\n"
                "Current rate: NOK 12,500 per consultant day (canonical-pricing.md). "
                "The source-system count is missing and must be confirmed. "
                f"Intended path: {EXPECTED_PATH}. The proposal remains an unsent draft."
            ),
        },
    }
    tool = {
        "type": "item.started",
        "item": {"type": "command_execution", "name": "exec_command"},
    }
    return [json.dumps(assistant), json.dumps(tool)]


def _parse_run(
    lines: list[str], started: float, *, treatment: bool,
    event_times: list[float] | None = None,
) -> dict[str, Any]:
    parsed = parse_event_stream(lines, start_monotonic=started, event_times=event_times)
    answer = ""
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind, text_value, _ = extract_event(event) if isinstance(event, dict) else (None, None, None)
        if kind == "assistant" and text_value:
            answer = text_value
    result: dict[str, Any] = {
        "event_count": parsed["event_count"],
        "events": parsed["events"],
        "first_assistant_ms": (
            parsed["first_assistant"]["elapsed_ms"] if parsed["first_assistant"] else None
        ),
        "first_tool_ms": parsed["first_tool"]["elapsed_ms"] if parsed["first_tool"] else None,
        "first_tool_before_assistant": bool(
            parsed["first_tool"] and parsed["first_assistant"]
            and parsed["first_tool"]["order"] < parsed["first_assistant"]["order"]
        ),
        "advice_id_before_first_tool": parsed["advice_id_before_first_tool"],
        "deterministic_indicators": score_answer(answer),
    }
    if treatment:
        result["advice_id"] = parsed["advice_id"]
    return result


def _collect_codex_events(
    process: subprocess.Popen[bytes], *, started: float, timeout: int
) -> tuple[list[str], list[float], str | None]:
    """Read bounded JSONL output while recording arrival time for each line."""
    if process.stdout is None:
        return [], [], "codex_unavailable"
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = bytearray()
    lines: list[str] = []
    observed: list[float] = []
    total = 0
    failure: str | None = None
    deadline = started + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "timeout"
                break
            ready = selector.select(min(remaining, 0.25))
            for key, _ in ready:
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
                    line = bytes(pending[:newline])
                    del pending[:newline + 1]
                    lines.append(line.decode("utf-8", errors="replace"))
                    observed.append(time.monotonic())
            if failure:
                break
        if pending and failure is None:
            lines.append(bytes(pending).decode("utf-8", errors="replace"))
            observed.append(time.monotonic())
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
    return lines, observed, None


def _run_codex(
    *, codex: str, model: str, fixture: Path, home: Path,
    timeout: int, treatment: bool,
) -> dict[str, Any]:
    codex_home = home / ".codex"
    codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    auth_source_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    source_auth = auth_source_home / "auth.json"
    try:
        auth_ok = _copy_auth(source_auth, codex_home / "auth.json")
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

    env = {
        key: value for key, value in os.environ.items()
        if key not in {"OPENROUTER_API_KEY", "CODEX_HOME", "PYTHONPATH"}
    }
    env.update({
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={home / 'no-session-bus'}",
        "GNOME_KEYRING_CONTROL": str(home / "no-keyring"),
        "PYTHONPATH": os.pathsep.join(
            [str(isolated_python), str(ROOT / "src")]
        ),
    })
    command = [
        codex, "exec", "--json", "--ephemeral", "--sandbox", "read-only",
        "--skip-git-repo-check", "--dangerously-bypass-hook-trust",
        "--model", model, PROMPT,
    ]
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=fixture,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        lines, event_times, failure = _collect_codex_events(
            process, started=started, timeout=timeout
        )
    except OSError:
        return {"status": "failed", "failure": "codex_unavailable"}
    if failure:
        return {"status": "failed", "failure": failure}
    parsed = _parse_run(
        lines, started, treatment=treatment, event_times=event_times
    )
    metric_path = home / ".local" / "state" / "jevcompass" / "advisor.jsonl"
    metrics = read_safe_metrics(metric_path)
    if treatment:
        parsed["advice_metric"] = correlate_advice(
            parse_event_stream(lines, start_monotonic=started, event_times=event_times), metrics
        )
        parsed["jev_metrics"] = metrics
    parsed.update({
        "status": "completed" if process.returncode == 0 else "failed",
        "exit_code": process.returncode,
    })
    if process.returncode != 0:
        parsed["failure"] = "codex_nonzero_exit"
    return parsed


def _mock_arm(*, treatment: bool) -> dict[str, Any]:
    lines = _safe_mock_lines()
    if not treatment:
        event = json.loads(lines[0])
        event["item"]["text"] = TRACE_RE.sub("", event["item"]["text"])
        lines[0] = json.dumps(event)
    started = time.monotonic()
    result = _parse_run(lines, started, treatment=treatment)
    result.update({"status": "completed", "exit_code": 0, "mock": True})
    if treatment:
        result["advice_metric"] = correlate_advice(
            parse_event_stream(lines, start_monotonic=started),
            [{
                "event": "UserPromptSubmit",
                "category": "source-review",
                "status": "local",
                "duration_ms": 2.5,
                "trace": "abcdef12",
            }],
        )
        result["jev_metrics"] = [{
            "event": "UserPromptSubmit", "category": "source-review",
            "status": "local", "duration_ms": 2.5, "trace": "abcdef12",
        }]
    return result


def run_pair(*, mode: str, model: str | None, timeout: int) -> dict[str, Any]:
    if timeout < 1 or timeout > MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if not FIXTURE.is_dir():
        raise FileNotFoundError("synthetic W02 fixture is unavailable")
    auth_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    auth_available = (auth_home / "auth.json").is_file()
    if mode == "run" and not auth_available:
        raise RuntimeError("Codex auth.json is unavailable")
    codex = shutil.which("codex") if mode == "run" else None
    if mode == "run" and not codex:
        raise RuntimeError("Codex CLI is unavailable")

    order = ["baseline", "treatment"]
    random.SystemRandom().shuffle(order)
    summary: dict[str, Any] = {
        "case_id": "W02",
        "status": "completed",
        "mode": mode,
        "arm_order": order,
        "model": model if mode == "run" else None,
        "timeout_seconds": timeout,
        "openrouter_key_forwarded": False,
        "evaluator_method": "deterministic indicators; not blinded expert assessment",
    }
    with tempfile.TemporaryDirectory(prefix="jevcompass-w02-") as temporary:
        work = Path(temporary)
        arms: dict[str, dict[str, Any]] = {}
        digest_values: list[str] = []
        for label in ("baseline", "treatment"):
            arm_home = work / label
            arm_home.mkdir(mode=0o700)
            fixture_path = work / f"{label}-fixture"
            digest_values.append(copy_fixture(FIXTURE, fixture_path))
            arms[label] = {"home": arm_home, "fixture": fixture_path}
        if digest_values[0] != digest_values[1]:
            raise RuntimeError("paired fixture copies differ")
        summary["fixture_copies_identical"] = True
        summary["fixture_sha256"] = digest_values[0]
        arm_results: dict[str, Any] = {}
        for label in order:
            treatment = label == "treatment"
            if mode == "dry-run":
                arm_results[label] = {
                    "status": "not_run",
                    "hooks_would_be_configured": treatment,
                    "auth_copied": False,
                    "model_called": False,
                }
            elif mode == "mock":
                arm_results[label] = _mock_arm(treatment=treatment)
            else:
                arm_results[label] = _run_codex(
                    codex=codex or "codex",
                    model=model or "",
                    fixture=arms[label]["fixture"],
                    home=arms[label]["home"],
                    timeout=timeout,
                    treatment=treatment,
                )
        summary["arms"] = arm_results
        summary["status"] = (
            "failed" if any(
                result.get("status") == "failed"
                for result in arm_results.values()
            ) else "completed"
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate setup; do not launch Codex")
    mode.add_argument("--mock", action="store_true", help="parse synthetic events; do not launch Codex")
    parser.add_argument("--model", help="same explicit Codex model for both arms (required for live run)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"per-arm timeout (max {MAX_TIMEOUT}s)")
    args = parser.parse_args(argv)
    selected_mode = "dry-run" if args.dry_run else "mock" if args.mock else "run"
    if selected_mode == "run" and not args.model:
        parser.error("--model is required for a live pair")
    try:
        result = run_pair(mode=selected_mode, model=args.model, timeout=args.timeout)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        # Error messages are fixed allowlisted phrases; do not expose paths or process output.
        print(json.dumps({"case_id": "W02", "status": "failed", "failure": str(error)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
