#!/usr/bin/env python3
"""Offline-first, bounded matched pair for opt-in spawn advice.

The default invocation is a local dry run: it verifies identical synthetic
fixtures and reviewed skills, plus hook configuration parity. ``--run`` is an
explicit, authenticated Codex CLI experiment. Raw event streams are never
written to disk or printed; missing child-level evidence is inconclusive.
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
FIXTURE = ROOT / "tests" / "fixtures" / "webhook_plan"
SKILL_NAME = "jevcompass-focused-tests"
SKILL_FILE = f"{SKILL_NAME}/SKILL.md"
FIXTURE_FILE = "WEBHOOK_BRIEF.md"
DEFAULT_TIMEOUT = 90
MAX_TIMEOUT = 120
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
SPAWN_MATCHER = "^(Agent|spawn_agent|collaborationspawn_agent)$"
SPAWN_TOOL_NAMES = {"Agent", "spawn_agent", "collaborationspawn_agent"}
ADVICE_ID_RE = re.compile(r"(?m)^JevCompass advice ID: ([a-f0-9]{8})[ \t]*$", re.I)
CONTEXT_SKILL_RE = re.compile(r"(?m)^- skill `jevcompass-focused-tests`:")
MAX_CONTEXT_EVIDENCE_CHARS = 8192
MAX_CONTEXT_CANDIDATE_LINES = 6
MAX_ADVICE_CONTEXTS = 16
MAX_DEVELOPER_CONTEXTS = 32
SAFE_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")

PROMPT = (
    "Spawn exactly one explorer child using task_name `plan_python_webhook_tests` "
    "to produce a short local Python test plan for the fictional webhook "
    "requirements. Have the child read the installed skill "
    f"`{SKILL_FILE}`, followed by `{FIXTURE_FILE}` as its first task-source "
    "read, and return a concise test plan. Do not edit files, access a network, "
    "or use other children."
)


def _hash_tree(root: Path) -> str:
    """Hash tree-relative names and bytes; reject symlinks and special files."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("synthetic input contains a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def fixture_digest(root: Path = FIXTURE) -> str:
    if not root.is_dir():
        raise FileNotFoundError("synthetic webhook fixture unavailable")
    return _hash_tree(root)


def _skill_content() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from jevcompass.skill_pack import _bundled_content

    return _bundled_content(SKILL_NAME)


def _install_reviewed_skill(codex_home: Path) -> str:
    """Install the same bundled, reviewed skill into an isolated Codex home."""
    skill_dir = codex_home / "skills" / SKILL_NAME
    skill_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    skill_path = skill_dir / "SKILL.md"
    with skill_path.open("x", encoding="utf-8") as stream:
        stream.write(_skill_content())
    os.chmod(skill_path, 0o600)
    return _hash_tree(codex_home / "skills")


def _copy_fixture(source: Path, destination: Path) -> str:
    if not source.is_dir() or not (source / FIXTURE_FILE).is_file():
        raise FileNotFoundError("synthetic webhook fixture unavailable")
    shutil.copytree(source, destination, symlinks=False)
    return fixture_digest(destination)


def _install_hooks(home: Path, *, treatment: bool) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "src"))
    from jevcompass.installer import install

    install(home=home, spawn_advice=treatment)
    config = json.loads((home / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("hooks"), dict):
        raise ValueError("isolated hook configuration invalid")
    return config


def _strip_spawn_hook(config: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(config))
    hooks = result["hooks"]
    groups = hooks.get("PreToolUse", [])
    kept = [group for group in groups if group.get("matcher") != SPAWN_MATCHER]
    if kept:
        hooks["PreToolUse"] = kept
    else:
        hooks.pop("PreToolUse", None)
    return result


def verify_hook_pair(baseline: dict[str, Any], treatment: dict[str, Any]) -> bool:
    """Require equal default hooks and exactly the installer-owned narrow opt-in."""
    if baseline != _strip_spawn_hook(treatment):
        return False
    baseline_groups = baseline.get("hooks", {}).get("PreToolUse", [])
    if any(group.get("matcher") == SPAWN_MATCHER for group in baseline_groups):
        return False
    treatment_groups = [
        group for group in treatment.get("hooks", {}).get("PreToolUse", [])
        if group.get("matcher") == SPAWN_MATCHER
    ]
    if len(treatment_groups) != 1:
        return False
    handlers = treatment_groups[0].get("hooks", [])
    sys.path.insert(0, str(ROOT / "src"))
    from jevcompass.advisor import hook_command

    return (
        len(handlers) == 1
        and handlers[0] == {"type": "command", "command": hook_command(), "timeout": 2}
    )


def _copy_auth(source: Path, destination: Path) -> bool:
    if not source.is_file() or source.is_symlink():
        return False
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o600)
    return stat.S_IMODE(destination.stat().st_mode) == 0o600


def isolated_environment(home: Path, *, source_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return fresh per-arm HOME/CODEX_HOME/XDG locations and minimal env."""
    source = source_env if source_env is not None else os.environ
    env = {key: source[key] for key in (
        "PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
    ) if key in source}
    codex_home = home / ".codex"
    env.update({
        "HOME": str(home),
        "CODEX_HOME": str(codex_home),
        "PYTHONPATH": str(ROOT / "src"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
    })
    return env


def _event_item(event: dict[str, Any]) -> dict[str, Any]:
    item = event.get("item")
    if not isinstance(item, dict):
        item = event.get("payload")
    return item if isinstance(item, dict) else event


def _item_text(item: dict[str, Any]) -> str:
    if isinstance(item.get("text"), str):
        return item["text"]
    content = item.get("content")
    if isinstance(content, list):
        return "\n".join(
            part["text"] for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _message_role(item: dict[str, Any]) -> str:
    role = item.get("role")
    return role.lower() if isinstance(role, str) else ""


def _is_tool(item: dict[str, Any], event: dict[str, Any]) -> bool:
    kind = str(item.get("type") or item.get("item_type") or "").lower()
    if kind in {
        "command_execution", "function_call", "tool_call", "mcp_tool_call",
        "collaboration_tool_call", "custom_tool_call", "web_search",
    }:
        return True
    return event.get("type") in {"item.started", "item.completed"} and bool(kind) and kind not in {
        "reasoning", "agent_message", "assistant_message", "message", "error",
    }


def _read_evidence(item: dict[str, Any]) -> tuple[bool, bool]:
    """Inspect tool arguments in memory only; emit booleans, never raw values."""
    args = {
        key: item[key]
        for key in ("input", "arguments", "tool_input", "command")
        if key in item
    }
    blob = json.dumps(args, ensure_ascii=False)
    skill = bool(re.search(
        r"(?<![A-Za-z0-9_.-])\.codex/skills/jevcompass-focused-tests/SKILL\.md(?![A-Za-z0-9_.-])",
        blob,
        re.I,
    ))
    fixture = bool(re.search(
        r"(?<![A-Za-z0-9_.-])WEBHOOK_BRIEF\.md(?![A-Za-z0-9_.-])",
        blob,
        re.I,
    ))
    return skill, fixture


def _selected_known_skill(context: str) -> bool:
    """Inspect only the advisor's bounded candidate bullet list for our known skill."""
    candidate_lines = 0
    for line in context[:MAX_CONTEXT_EVIDENCE_CHARS].splitlines():
        if not line.startswith("- "):
            continue
        candidate_lines += 1
        if candidate_lines > MAX_CONTEXT_CANDIDATE_LINES:
            break
        if CONTEXT_SKILL_RE.match(line):
            return True
    return False


def _session_meta(path: Path) -> dict[str, Any] | None:
    """Read only the first JSONL record when it is a session metadata row."""
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            first = json.loads(next(stream, ""))
    except (OSError, StopIteration, json.JSONDecodeError):
        return None
    if not isinstance(first, dict) or first.get("type") != "session_meta":
        return None
    payload = first.get("payload")
    return payload if isinstance(payload, dict) else None


def _session_files(codex_home: Path) -> list[Path]:
    """Find regular session logs without following symlinks."""
    sessions = codex_home / "sessions"
    if not sessions.is_dir() or sessions.is_symlink():
        return []
    return sorted(
        path for path in sessions.rglob("*.jsonl")
        if path.is_file() and not path.is_symlink()
    )


def parse_persisted_child_sessions(
    paths: Iterable[Path], *, treatment: bool, spawn_trace_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Summarize one child session linked to one root; never return transcript data."""
    sessions: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        meta = _session_meta(path)
        if meta is not None:
            sessions.append((path, meta))

    roots = [
        (path, meta) for path, meta in sessions
        if isinstance(meta.get("id"), str) and SAFE_ID_RE.fullmatch(meta["id"])
        and not meta.get("forked_from_id")
        and not meta.get("agent_nickname")
        and not meta.get("agent_path")
    ]
    if len(roots) != 1:
        return {"status": "inconclusive", "reason": "root_session_identity_ambiguous"}
    root_path, root_meta = roots[0]
    root_id = root_meta["id"]
    children = [
        (path, meta) for path, meta in sessions
        if path != root_path
        and meta.get("forked_from_id") == root_id
        and isinstance(meta.get("id"), str) and SAFE_ID_RE.fullmatch(meta["id"])
        and isinstance(meta.get("agent_nickname"), str) and bool(meta["agent_nickname"])
        and isinstance(meta.get("agent_path"), str) and bool(meta["agent_path"])
        # Codex may name the path after the task, not the agent's explorer role.
    ]
    linked = [
        (path, meta) for path, meta in sessions
        if path != root_path and meta.get("forked_from_id") == root_id
    ]
    if len(linked) != 1 or len(children) != 1:
        return {"status": "inconclusive", "reason": "child_session_identity_ambiguous"}

    child_path, _child_meta = children[0]
    valid_spawn_traces = {
        trace.lower() for trace in spawn_trace_ids
        if isinstance(trace, str) and re.fullmatch(r"[a-f0-9]{8}", trace, re.I)
    }
    try:
        with child_path.open(encoding="utf-8", errors="replace") as stream:
            return _summarize_child_lines(
                stream, treatment=treatment, spawn_trace_ids=valid_spawn_traces,
            )
    except OSError:
        return {"status": "inconclusive", "reason": "child_session_unreadable"}


def _summarize_child_lines(
    lines: Iterable[str], *, treatment: bool, spawn_trace_ids: set[str],
) -> dict[str, Any]:
    """Reduce child events to evidence booleans and safe catalog IDs."""
    first_context_order: int | None = None
    advice_contexts: dict[str, bool] = {}
    advice_contexts_truncated = False
    developer_context_count = 0
    first_tool: int | None = None
    skill_read_order: int | None = None
    fixture_read_order: int | None = None
    for index, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        item = _event_item(event)
        kind = str(item.get("type") or item.get("item_type") or "").lower()
        if kind == "message" and _message_role(item) == "developer" and first_tool is None:
            if first_context_order is None:
                first_context_order = index
            developer_context_count += 1
            if developer_context_count <= MAX_DEVELOPER_CONTEXTS:
                text = _item_text(item)[:MAX_CONTEXT_EVIDENCE_CHARS]
                match = ADVICE_ID_RE.search(text)
                if match:
                    advice_id = match.group(1).lower()
                    selected = _selected_known_skill(text)
                    if advice_id in advice_contexts:
                        advice_contexts[advice_id] = advice_contexts[advice_id] or selected
                    elif len(advice_contexts) < MAX_ADVICE_CONTEXTS:
                        advice_contexts[advice_id] = selected
                    else:
                        advice_contexts_truncated = True
            else:
                advice_contexts_truncated = True
        if _is_tool(item, event):
            first_tool = first_tool or index
            saw_skill, saw_fixture = _read_evidence(item)
            if saw_skill and skill_read_order is None:
                skill_read_order = index
            if saw_fixture and fixture_read_order is None:
                fixture_read_order = index
    verified_order = bool(first_context_order and first_tool and first_context_order < first_tool)
    reads_observed = bool(
        skill_read_order is not None and fixture_read_order is not None
        and skill_read_order < fixture_read_order
    )
    evidence_complete = bool(verified_order and reads_observed)
    if treatment:
        report: dict[str, Any] | None = None
        if len(spawn_trace_ids) == 1:
            spawn_trace = next(iter(spawn_trace_ids))
            if spawn_trace in advice_contexts:
                report = {
                    "advice_id": spawn_trace,
                    "selected_ids": [SKILL_NAME] if advice_contexts[spawn_trace] else [],
                    "before_first_tool": True,
                }
        advice_complete = bool(
            report and SKILL_NAME in report["selected_ids"] and not advice_contexts_truncated
        )
        status = "confirmed" if evidence_complete and advice_complete else "inconclusive"
        if status == "confirmed":
            reason = None
        elif advice_contexts_truncated:
            reason = "advice_contexts_ambiguous"
        elif len(spawn_trace_ids) != 1:
            reason = "spawn_metric_ambiguous_or_missing"
        elif report is None:
            reason = "spawn_advice_not_in_initial_context"
        elif SKILL_NAME not in report["selected_ids"]:
            reason = "spawn_advice_missing_required_skill"
        else:
            reason = "child_read_order_not_observed"
    else:
        status = "confirmed" if evidence_complete and not spawn_trace_ids else "inconclusive"
        reason = None if status == "confirmed" else (
            "unexpected_spawn_metric" if spawn_trace_ids else "child_read_order_not_observed"
        )
        report = None
    return {
        "status": status,
        "reason": reason,
        "child_session_observed": True,
        "advice_report": report,
        "advice_context_count": len(advice_contexts),
        "developer_context_count": min(developer_context_count, MAX_DEVELOPER_CONTEXTS + 1),
        "advice_contexts_truncated": advice_contexts_truncated,
        "skill_read_observed": skill_read_order is not None,
        "first_fixture_read_observed": bool(
            fixture_read_order is not None and skill_read_order is not None
            and skill_read_order < fixture_read_order
        ),
        "initial_context_before_first_tool": verified_order,
    }


def _safe_metrics(path: Path) -> list[dict[str, Any]]:
    allowed = {"local", "jev", "cache", "skip", "insufficient-candidates", "low-signal-skip", "classification-skip", "role-skip"}
    result: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-64:]
    except OSError:
        return result
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (isinstance(record, dict) and record.get("event") in {"PreToolUse", "SubagentStart"}
                and record.get("status") in allowed):
            trace = record.get("trace")
            result.append({
                "event": record["event"], "status": record["status"],
                "trace": trace if isinstance(trace, str) and re.fullmatch(r"[a-f0-9]{8}", trace) else None,
            })
    return result


def _collect(process: subprocess.Popen[bytes], timeout: int) -> tuple[list[str], str | None]:
    if process.stdout is None:
        return [], "cli_output_unavailable"
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    # Drain the CLI stream to prevent pipe deadlock, but discard it: child
    # evidence comes from the isolated persisted sessions below.
    lines: list[str] = []
    total = 0
    failure = None
    deadline = time.monotonic() + timeout
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
                if total > MAX_OUTPUT_BYTES:
                    failure = "output_limit"
                    break
            if failure:
                break
    finally:
        selector.close()
        process.stdout.close()
    if failure:
        process.kill()
        process.wait()
        return [], failure
    try:
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return [], "timeout"
    return lines, None


def prepare_pair(root: Path, *, auth_source: Path | None = None, live: bool = False) -> dict[str, Any]:
    """Build two isolated profiles and verify parity before any live call."""
    source_digest = fixture_digest()
    order = ["baseline", "treatment"]
    random.SystemRandom().shuffle(order)
    profiles: dict[str, dict[str, Any]] = {}
    fixture_digests: list[str] = []
    skill_digests: list[str] = []
    configs: dict[str, dict[str, Any]] = {}
    for arm in ("baseline", "treatment"):
        home = root / arm / "home"
        home.mkdir(mode=0o700, parents=True)
        codex_home = home / ".codex"
        codex_home.mkdir(mode=0o700)
        env = isolated_environment(home)
        for path_key in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
            Path(env[path_key]).mkdir(mode=0o700, parents=True)
        fixture = root / arm / "fixture"
        fixture_digests.append(_copy_fixture(FIXTURE, fixture))
        skill_digests.append(_install_reviewed_skill(codex_home))
        treatment = arm == "treatment"
        configs[arm] = _install_hooks(home, treatment=treatment)
        auth_ok = False
        if live and auth_source is not None:
            auth_ok = _copy_auth(auth_source, codex_home / "auth.json")
            if not auth_ok:
                raise RuntimeError("auth unavailable or could not be copied safely")
        profiles[arm] = {
            "home": home, "codex_home": codex_home, "fixture": fixture,
            "env": env, "auth_ok": auth_ok,
        }
    hook_parity = verify_hook_pair(configs["baseline"], configs["treatment"])
    if not (len(set(fixture_digests)) == 1 == len(set(skill_digests)) and hook_parity):
        raise RuntimeError("paired profile parity check failed")
    return {
        "order": order, "profiles": profiles,
        "fixture_sha256": source_digest,
        "fixture_copies_identical": len(set(fixture_digests)) == 1,
        "skill_sha256": skill_digests[0],
        "skill_copies_identical": len(set(skill_digests)) == 1,
        "default_configuration_equal": hook_parity,
        "treatment_matcher": SPAWN_MATCHER,
        "auth_copied": live and all(item["auth_ok"] for item in profiles.values()),
    }


def _run_arm(*, codex: str, model: str, arm: str, profile: dict[str, Any], timeout: int) -> dict[str, Any]:
    codex_home = profile["codex_home"]
    auth = codex_home / "auth.json"
    if not auth.is_file() or stat.S_IMODE(auth.stat().st_mode) != 0o600:
        return {"status": "failed", "reason": "auth_mode_invalid"}
    command = [
        codex, "exec", "--json", "--sandbox", "read-only",
        "--skip-git-repo-check", "--dangerously-bypass-hook-trust", "--enable",
        "multi_agent", "--model", model, PROMPT,
    ]
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command, cwd=profile["fixture"], env=profile["env"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        lines, failure = _collect(process, timeout)
    except OSError:
        return {"status": "inconclusive", "reason": "codex_unavailable"}
    if failure:
        return {"status": "inconclusive", "reason": failure}
    if process.returncode != 0:
        return {"status": "inconclusive", "reason": "codex_nonzero_exit", "exit_code": process.returncode}
    # The root --json stream does not reliably contain child events. Read the
    # isolated profile's persisted session files and retain only a safe summary.
    metrics = _safe_metrics(codex_home.parent / ".local" / "state" / "jevcompass" / "advisor.jsonl")
    pretool_metrics = [item for item in metrics if item["event"] == "PreToolUse"]
    pretool_trace_ids = sorted({item["trace"] for item in pretool_metrics if item["trace"]})
    summary = parse_persisted_child_sessions(
        _session_files(codex_home), treatment=arm == "treatment",
        spawn_trace_ids=pretool_trace_ids,
    )
    summary.update({
        "exit_code": process.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "pretool_spawn_metric_observed": bool(pretool_metrics),
        "pretool_spawn_metric_count": len(pretool_metrics),
        "pretool_spawn_trace_count": len(pretool_trace_ids),
    })
    if arm == "treatment":
        advice_id = (summary.get("advice_report") or {}).get("advice_id")
        summary["spawn_advice_correlated_to_child"] = bool(
            advice_id and len(pretool_trace_ids) == 1 and advice_id == pretool_trace_ids[0]
        )
        # Delivery and task-source reads are separate outcomes. A correlated
        # child-context ID can be confirmed even if the child skips the brief.
        summary["delivery_status"] = (
            "confirmed" if summary["spawn_advice_correlated_to_child"] else "inconclusive"
        )
        if not summary["spawn_advice_correlated_to_child"]:
            summary["status"] = "inconclusive"
            summary["reason"] = "spawn_advice_not_correlated_to_child_context"
    if arm == "baseline":
        summary["delivery_status"] = "not_applicable"
    if arm == "baseline" and summary.get("pretool_spawn_metric_observed"):
        summary["status"] = "failed"
        summary["reason"] = "unexpected_pretool_metric_in_baseline"
    return summary


def run_pair(*, live: bool, model: str | None, timeout: int, auth_source: Path | None = None) -> dict[str, Any]:
    if timeout < 1 or timeout > MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if live and (not model or not model.strip()):
        raise ValueError("--model is required with --run")
    if live and auth_source is None:
        configured_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        auth_source = configured_home / "auth.json"
    with tempfile.TemporaryDirectory(prefix="jevcompass-spawn-pair-") as temporary:
        root = Path(temporary)
        plan = prepare_pair(root, auth_source=auth_source, live=live)
        result: dict[str, Any] = {
            "case": "synthetic-webhook-spawn-advice",
            "status": "dry_run_verified" if not live else "completed",
            "mode": "run" if live else "dry-run",
            "arm_order": plan["order"],
            "timeout_seconds_per_arm": timeout,
            "fixture_sha256": plan["fixture_sha256"],
            "fixture_copies_identical": plan["fixture_copies_identical"],
            "skill_sha256": plan["skill_sha256"],
            "skill_copies_identical": plan["skill_copies_identical"],
            "default_configuration_equal": plan["default_configuration_equal"],
            "treatment_matcher": plan["treatment_matcher"],
            "auth_copied_0600": plan["auth_copied"],
            "network_or_model_called": False,
        }
        if not live:
            return result
        executable = shutil.which("codex")
        if not executable:
            result.update({"status": "failed", "reason": "codex_cli_unavailable"})
            return result
        result["network_or_model_called"] = True
        arms: dict[str, Any] = {}
        for label in plan["order"]:
            arms[label] = _run_arm(
                codex=executable, model=model or "", arm=label,
                profile=plan["profiles"][label], timeout=timeout,
            )
        result["arms"] = arms
        if any(item.get("status") == "failed" for item in arms.values()):
            result["status"] = "failed"
        elif any(item.get("status") != "confirmed" for item in arms.values()):
            result["status"] = "inconclusive"
            result["reason"] = "child transcript evidence incomplete or unobservable"
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="verify offline fixture, skill, and hook parity (default)")
    mode.add_argument("--run", action="store_true", help="explicitly run two authenticated Codex CLI arms")
    parser.add_argument("--model", help="same explicit Codex model for both live arms")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"per-arm timeout (maximum {MAX_TIMEOUT}s)")
    parser.add_argument("--auth-file", type=Path, help="auth.json to copy into each isolated arm (live mode only)")
    args = parser.parse_args(argv)
    if args.model and not args.run:
        parser.error("--model is accepted only with --run")
    try:
        result = run_pair(live=args.run, model=args.model, timeout=args.timeout, auth_source=args.auth_file)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        # Do not echo exception text: filesystem paths or process data can appear there.
        result = {"case": "synthetic-webhook-spawn-advice", "status": "failed", "reason": "setup_failed"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "dry_run_verified" else (0 if result["status"] == "completed" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
