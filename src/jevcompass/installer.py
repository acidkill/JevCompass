"""Idempotent user-scoped installation and Codex hook registration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
import tempfile
import time
from typing import Any

from . import advisor
from .paths import resolve_codex_home


LEGACY_GATE_COMMANDS = {"npx -y jev-use hook gate", "jev-use hook gate"}
ADVISOR_EVENTS = ("UserPromptSubmit", "SubagentStart")
SUBAGENT_MATCHER = "^(explorer|worker)$"
SPAWN_ADVICE_MATCHER = "^(Agent|spawn_agent|collaborationspawn_agent)$"


def _is_product_hook(command: str) -> bool:
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return (len(words) >= 3 and words[-3:] == ["-m", "jevcompass", "hook"] or
            len(words) == 1 and Path(words[0]).name == "codex-jev-advisor")


def _is_legacy_gate(command: str) -> bool:
    stripped = command.strip()
    if stripped in LEGACY_GATE_COMMANDS:
        return True
    try:
        words = shlex.split(stripped)
    except ValueError:
        return False
    return len(words) == 1 and Path(words[0]).name == "codex-jev-risk-router"


def _is_spawn_advice_group(group: dict[str, Any], command: str) -> bool:
    if group.get("matcher") != SPAWN_ADVICE_MATCHER:
        return False
    handlers = group.get("hooks", [])
    return isinstance(handlers, list) and any(
        isinstance(handler, dict)
        and (handler.get("command") == command or _is_product_hook(handler.get("command", "")))
        for handler in handlers
    )


def merge_hooks(
    data: dict[str, Any], command: str, spawn_advice: bool | None = None,
) -> dict[str, Any]:
    """Return a config copy with only JevCompass hooks added and legacy Jev gate removed."""
    result = json.loads(json.dumps(data))
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks.json: 'hooks' must be an object")

    existing_spawn_advice = False
    for event in ("PreToolUse", *ADVISOR_EVENTS):
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"hooks.json: '{event}' must be an array")
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            handlers = group.get("hooks", [])
            if not isinstance(handlers, list):
                kept_groups.append(group)
                continue
            is_spawn_group = event == "PreToolUse" and _is_spawn_advice_group(group, command)
            existing_spawn_advice = existing_spawn_advice or is_spawn_group
            retained = []
            for handler in handlers:
                if not isinstance(handler, dict):
                    retained.append(handler)
                    continue
                current = handler.get("command", "")
                if event == "PreToolUse":
                    remove = _is_legacy_gate(current) or (is_spawn_group and (
                        current == command or _is_product_hook(current)
                    ))
                else:
                    remove = current == command or _is_product_hook(current)
                if not remove:
                    retained.append(handler)
            if retained:
                kept_groups.append({**group, "hooks": retained})
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)

    handler = {"type": "command", "command": command, "timeout": 2, "additionalContextLimit": 400}
    hooks.setdefault("UserPromptSubmit", []).append({"hooks": [handler]})
    hooks.setdefault("SubagentStart", []).append({
        "matcher": SUBAGENT_MATCHER,
        "hooks": [handler],
    })
    enable_spawn_advice = existing_spawn_advice if spawn_advice is None else spawn_advice
    if enable_spawn_advice:
        hooks.setdefault("PreToolUse", []).append({
            "matcher": SPAWN_ADVICE_MATCHER,
            "hooks": [{"type": "command", "command": command, "timeout": 2}],
        })
    return result


def _write_hooks(path: Path, data: dict[str, Any]) -> Path | None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    new_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == new_text:
        return None
    backup = None
    old_mode = 0o600
    if path.exists():
        old_mode = stat.S_IMODE(path.stat().st_mode)
        backup = path.with_name(f"hooks.json.bak-jevcompass-{time.time_ns()}")
        shutil.copy2(path, backup)
    fd, temporary = tempfile.mkstemp(prefix=".hooks-jevcompass-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(new_text)
        os.chmod(temporary, old_mode)
        json.loads(Path(temporary).read_text(encoding="utf-8"))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return backup


def install(
    *, dry_run: bool = False, home: Path | None = None, spawn_advice: bool | None = None,
) -> str:
    """Register standard hooks and optionally opt in to spawn advice."""
    config_home = home / ".codex" if home is not None else resolve_codex_home()
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required")
    hooks_path = config_home / "hooks.json"
    if hooks_path.exists():
        try:
            current = json.loads(hooks_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("hooks.json is unreadable or invalid JSON; no files were changed") from error
    else:
        current = {"hooks": {}}
    if not isinstance(current, dict):
        raise ValueError("hooks.json must contain a JSON object")
    command = advisor.hook_command()
    merged = merge_hooks(current, command, spawn_advice=spawn_advice)
    if dry_run:
        return "Install plan valid; no files changed"

    backup = _write_hooks(hooks_path, merged)
    if backup:
        return f"Installed advisory hooks; backed up existing hooks.json to {backup.name}"
    return "Installed advisory hooks"
