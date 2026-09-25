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
ADVICE_RE = re.compile(
    r"(?m)^JevCompass advice ID: (none|[a-f0-9]{8}); selected catalog IDs: "
    r"(none|[a-z0-9_.-]+(?:,[a-z0-9_.-]+){0,5})$", re.I,
)
SAFE_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")

PROMPT = (
    "Spawn exactly one explorer child using task_name `plan_python_webhook_tests` "
    "to produce a short local Python test plan for the fictional webhook "
    "requirements. Before spawning, pass through any "
    "JevCompass advice ID and selected catalog IDs visible to you; never invent "
    "them. The child must first report exactly what JevCompass advice ID and "
    "selected catalog IDs it received in its own initial context, before any "
    "tool call, using this line format: `JevCompass advice ID: <8 lowercase "
    "hex characters or none>; selected catalog IDs: <comma-separated IDs or "
    "none>`. Then have the child read the installed skill "
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


def _child_id(event: dict[str, Any], item: dict[str, Any]) -> str | None:
    for source in (event, item):
        for key in ("agent_id",):
            value = source.get(key)
            if isinstance(value, str) and SAFE_ID_RE.fullmatch(value):
                return value
        agent = source.get("agent")
        if isinstance(agent, dict):
            for key in ("agent_id", "id"):
                value = agent.get(key)
                if isinstance(value, str) and SAFE_ID_RE.fullmatch(value):
                    return value
    return None


def _explicit_child_role(event: dict[str, Any], item: dict[str, Any]) -> bool:
    """Require a host child-role marker; an arbitrary root agent_id is not proof."""
    for source in (event, item):
        if any(
            isinstance(source.get(key), str) and source[key].lower() == "explorer"
            for key in ("agent_type", "agent_name")
        ):
            return True
        path = source.get("agent_path")
        if isinstance(path, str) and path.rstrip("/").endswith("/explorer"):
            return True
        agent = source.get("agent")
        if isinstance(agent, dict):
            if any(
                isinstance(agent.get(key), str) and agent[key].lower() == "explorer"
                for key in ("agent_type", "agent_name", "name")
            ):
                return True
            path = agent.get("path")
            if isinstance(path, str) and path.rstrip("/").endswith("/explorer"):
                return True
    return False


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


def _is_tool(item: dict[str, Any], event: dict[str, Any]) -> bool:
    kind = str(item.get("type") or item.get("item_type") or "").lower()
    if kind in {
        "command_execution", "function_call", "tool_call", "mcp_tool_call",
        "collaboration_tool_call", "web_search",
    }:
        return True
    return event.get("type") in {"item.started", "item.completed"} and bool(kind) and kind not in {
        "reasoning", "agent_message", "assistant_message", "message", "error",
    }


def _read_evidence(item: dict[str, Any]) -> tuple[bool, bool]:
    """Inspect tool arguments in memory only; emit booleans, never raw values."""
    blob = json.dumps(item, ensure_ascii=False).lower()
    return SKILL_FILE.lower() in blob, FIXTURE_FILE.lower() in blob


def parse_child_transcript(lines: Iterable[str]) -> dict[str, Any]:
    """Summarize explicit child ID, first response/tool order, and observed reads."""
    parsed: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    spawn_observed = False
    for index, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        item = _event_item(event)
        tool_name = item.get("name") or item.get("tool_name") or item.get("tool")
        spawn_observed = spawn_observed or (
            isinstance(tool_name, str) and tool_name in SPAWN_TOOL_NAMES
        )
        parsed.append((index, event, item))

    # CLI formats vary. A stable agent_id must occur on a child assistant event;
    # agent_type/name alone is not enough to prove that these are child events.
    child_events = [
        (n, e, i, _child_id(e, i)) for n, e, i in parsed
        if _child_id(e, i) and _explicit_child_role(e, i)
    ]
    child_id = child_events[0][3] if child_events else None
    if child_id is None:
        return {
            "status": "inconclusive", "reason": "child_identity_unobservable",
            "spawn_tool_observed": spawn_observed,
        }

    first_assistant: tuple[int, str] | None = None
    first_tool: int | None = None
    skill_read_order: int | None = None
    fixture_read_order: int | None = None
    for order, event, item, identity in child_events:
        if identity != child_id:
            continue
        kind = str(item.get("type") or item.get("item_type") or "").lower()
        if kind in {"agent_message", "assistant_message", "message"}:
            if first_assistant is None:
                first_assistant = (order, _item_text(item))
        if _is_tool(item, event):
            first_tool = first_tool or order
            saw_skill, saw_fixture = _read_evidence(item)
            if saw_skill and skill_read_order is None:
                skill_read_order = order
            if saw_fixture and fixture_read_order is None:
                fixture_read_order = order

    report: dict[str, Any] | None = None
    if first_assistant:
        match = ADVICE_RE.search(first_assistant[1])
        if match:
            advice, selected = match.groups()
            report = {
                "advice_id": None if advice.lower() == "none" else advice.lower(),
                "selected_ids": [] if selected.lower() == "none" else list(dict.fromkeys(selected.split(","))),
                "before_first_tool": first_tool is None or first_assistant[0] < first_tool,
            }
    verified_order = bool(
        spawn_observed and first_assistant and first_tool
        and first_assistant[0] < first_tool
    )
    reads_observed = bool(
        skill_read_order is not None and fixture_read_order is not None
        and skill_read_order < fixture_read_order
    )
    status = (
        "confirmed" if verified_order and report and report["before_first_tool"] and reads_observed
        else "inconclusive"
    )
    reason = None if status == "confirmed" else "child_advice_or_read_order_not_observed"
    return {
        "status": status,
        "reason": reason,
        "spawn_tool_observed": spawn_observed,
        "child_id": child_id,
        "advice_report": report,
        "skill_read_observed": skill_read_order is not None,
        "first_fixture_read_observed": bool(
            fixture_read_order is not None and skill_read_order is not None
            and skill_read_order < fixture_read_order
        ),
        "first_assistant_before_tool": verified_order,
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
    pending = bytearray()
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
                pending.extend(chunk)
                while True:
                    newline = pending.find(b"\n")
                    if newline < 0:
                        break
                    lines.append(bytes(pending[:newline]).decode("utf-8", errors="replace"))
                    del pending[:newline + 1]
            if failure:
                break
    finally:
        selector.close()
        process.stdout.close()
    if failure:
        process.kill()
        process.wait()
        return [], failure
    if pending:
        lines.append(bytes(pending).decode("utf-8", errors="replace"))
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
        codex, "exec", "--json", "--ephemeral", "--sandbox", "read-only",
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
        return {"status": "failed", "reason": "codex_unavailable"}
    if failure:
        return {"status": "failed", "reason": failure}
    if process.returncode != 0:
        return {"status": "failed", "reason": "codex_nonzero_exit", "exit_code": process.returncode}
    summary = parse_child_transcript(lines)
    metrics = _safe_metrics(codex_home.parent / ".local" / "state" / "jevcompass" / "advisor.jsonl")
    summary.update({
        "exit_code": process.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "pretool_spawn_metric_observed": any(item["event"] == "PreToolUse" for item in metrics),
        "pretool_spawn_trace_ids": [item["trace"] for item in metrics if item["event"] == "PreToolUse" and item["trace"]],
    })
    if arm == "treatment":
        advice_id = (summary.get("advice_report") or {}).get("advice_id")
        summary["spawn_advice_correlated_to_child"] = bool(
            advice_id and advice_id in summary["pretool_spawn_trace_ids"]
        )
        if not summary["spawn_advice_correlated_to_child"]:
            summary["status"] = "inconclusive"
            summary["reason"] = "spawn_advice_not_correlated_to_child_context"
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
