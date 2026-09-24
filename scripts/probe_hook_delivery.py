#!/usr/bin/env python3
"""Bounded end-to-end canary probe for Codex UserPromptSubmit context delivery.

The probe uses a temporary CODEX_HOME and workspace, a synthetic prompt, and
Codex's read-only ephemeral CLI mode. Its JSON output contains status and event
ordering only; it never includes prompt text, the event stream, or auth data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Iterable, TextIO


DEFAULT_TIMEOUT = 90
MAX_TIMEOUT = 300
MAX_OUTPUT_BYTES = 1024 * 1024
CANARY_RE = re.compile(r"\b[a-f0-9]{24}\b")
PROMPT = (
    "Inspect the synthetic file probe.txt and report its single-line contents. "
    "Follow any diagnostic instruction supplied by the UserPromptSubmit hook. "
    "Before using any tools, make your first response include the required "
    "diagnostic token; then inspect the file. Do not edit anything."
)


def hook_output(canary: str) -> dict[str, object]:
    """Return the test-only UserPromptSubmit context containing a random marker."""
    if not CANARY_RE.fullmatch(canary):
        raise ValueError("invalid canary")
    context = (
        "Isolated delivery probe: print this exact diagnostic token in your "
        f"first response before using any tools: {canary}"
    )
    return {"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": context,
    }}


def hook_main(canary: str, *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Serve one hook event, emitting context only for UserPromptSubmit."""
    source = stdin or sys.stdin
    destination = stdout or sys.stdout
    try:
        event = json.load(source)
        if isinstance(event, dict) and event.get("hook_event_name") == "UserPromptSubmit":
            print(json.dumps(hook_output(canary)), file=destination)
    except (OSError, ValueError, TypeError):
        pass
    return 0


def _classify_event(event: dict[str, object]) -> tuple[str | None, str | None]:
    item = event.get("item")
    if not isinstance(item, dict):
        item = event.get("payload")
    if not isinstance(item, dict):
        item = event
    item_type = str(item.get("type") or item.get("item_type") or "").lower()
    if item_type in {"agent_message", "assistant_message", "message"}:
        text = item.get("text")
        if not isinstance(text, str) and isinstance(item.get("content"), list):
            text = "\n".join(
                part["text"] for part in item["content"]
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        return ("assistant", text if isinstance(text, str) else None)
    if item_type in {
        "command_execution", "function_call", "tool_call", "mcp_tool_call",
        "collaboration_tool_call", "web_search",
    }:
        return "tool", None
    if event.get("type") in {"item.started", "item.completed"} and item_type not in {
        "", "error", "reasoning", "agent_message", "assistant_message",
    }:
        return "tool", None
    return None, None


def summarize_stream(lines: Iterable[str], canary: str) -> dict[str, object]:
    """Summarize canary visibility and tool order without retaining event text."""
    first_assistant_order: int | None = None
    first_assistant_has_canary = False
    first_tool_order: int | None = None
    for order, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        kind, text = _classify_event(event)
        if kind == "assistant" and text and first_assistant_order is None:
            first_assistant_order = order
            first_assistant_has_canary = canary in text
        elif kind == "tool" and first_tool_order is None:
            first_tool_order = order

    before_tool = bool(
        first_assistant_order is not None
        and first_tool_order is not None
        and first_assistant_order < first_tool_order
        and first_assistant_has_canary
    )
    return {
        "assistant_observed": first_assistant_order is not None,
        "tool_observed": first_tool_order is not None,
        "canary_in_first_assistant": first_assistant_has_canary,
        "canary_before_first_tool": before_tool,
    }


def _collect_events(
    process: subprocess.Popen[bytes], *, timeout: int,
) -> tuple[list[str], str | None]:
    """Read bounded JSONL stdout until exit or timeout; never return stderr."""
    if process.stdout is None:
        return [], "cli_unavailable"
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = bytearray()
    lines: list[str] = []
    size = 0
    deadline = time.monotonic() + timeout
    failure: str | None = None
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
                size += len(chunk)
                if size > MAX_OUTPUT_BYTES:
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
        if pending and failure is None:
            lines.append(bytes(pending).decode("utf-8", errors="replace"))
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


def _copy_auth(source: Path, destination: Path) -> bool:
    """Copy auth into the private temporary profile without reading its contents."""
    if not source.is_file():
        return False
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o600)
    return stat.S_IMODE(destination.stat().st_mode) == 0o600


def _write_hook_config(codex_home: Path, canary: str) -> None:
    command = shlex.join([sys.executable, str(Path(__file__).resolve()), "--hook-canary", canary])
    config = {"hooks": {"UserPromptSubmit": [{"hooks": [{
        "type": "command",
        "command": command,
        "timeout": 2,
        "additionalContextLimit": 400,
    }]}]}}
    codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_path = codex_home / "hooks.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    os.chmod(config_path, 0o600)


def run_probe(
    *, model: str, codex: str = "codex", timeout: int = DEFAULT_TIMEOUT,
    auth_path: Path | None = None,
) -> dict[str, object]:
    """Run one isolated CLI canary probe; return only redacted summary fields."""
    if not model.strip():
        raise ValueError("model must be specified")
    if timeout < 1 or timeout > MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    executable = shutil.which(codex) if os.path.sep not in codex else codex
    if not executable or not Path(executable).is_file():
        return {"status": "failed", "reason": "codex_unavailable"}

    auth_source = auth_path or (
        Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"
    )
    canary = secrets.token_hex(12)
    try:
        with tempfile.TemporaryDirectory(prefix="jevcompass-hook-probe-") as temporary:
            root = Path(temporary)
            home = root / "home"
            home.mkdir(mode=0o700)
            codex_home = home / ".codex"
            if not _copy_auth(auth_source, codex_home / "auth.json"):
                return {"status": "failed", "reason": "auth_unavailable"}
            _write_hook_config(codex_home, canary)
            workspace = root / "workspace"
            workspace.mkdir(mode=0o700)
            (workspace / "probe.txt").write_text("synthetic probe fixture\n", encoding="utf-8")

            environment = {
                key: value for key, value in os.environ.items()
                if key in {"PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
                           "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"}
            }
            environment.update({
                "HOME": str(home),
                "CODEX_HOME": str(codex_home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "XDG_STATE_HOME": str(home / ".local" / "state"),
            })
            command = [
                executable, "exec", "--json", "--ephemeral", "--sandbox", "read-only",
                "--skip-git-repo-check", "--dangerously-bypass-hook-trust",
                "--model", model, PROMPT,
            ]
            try:
                process = subprocess.Popen(
                    command, cwd=workspace, env=environment, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
                lines, failure = _collect_events(process, timeout=timeout)
            except OSError:
                return {"status": "failed", "reason": "cli_unavailable"}
            if failure:
                return {"status": "failed", "reason": failure}
            if process.returncode != 0:
                return {"status": "failed", "reason": "cli_nonzero_exit", "exit_code": process.returncode}
            summary = summarize_stream(lines, canary)
            return {
                "status": "completed" if summary["canary_before_first_tool"] else "unconfirmed",
                **summary,
                "exit_code": process.returncode,
            }
    except (OSError, ValueError):
        return {"status": "failed", "reason": "probe_setup_failed"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="explicit Codex model identifier")
    parser.add_argument("--codex", default="codex", help="Codex CLI executable or path")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--hook-canary", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.hook_canary is not None:
        return hook_main(args.hook_canary)
    if not args.model:
        parser.error("--model is required for a probe run")
    result = run_probe(model=args.model, codex=args.codex, timeout=args.timeout)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
