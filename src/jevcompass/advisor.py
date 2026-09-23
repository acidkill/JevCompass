#!/usr/bin/env python3
"""Non-blocking Codex hook advice from a locally curated Jev catalog."""

from __future__ import annotations

import hashlib
import argparse
import json
import os
import secrets
from pathlib import Path
import re
import sys
import time
from typing import Any

from .catalog import candidates, catalog_version
from .decisions import DecisionsClient, DecisionsError, configured_model


MAX_EVENT_BYTES = 256_000
MAX_CONTEXT_CHARS = 1_500
JEV_TIMEOUT = 1.5
MIN_CONFIDENCE = 0.5
CACHE_DIR = Path.home() / ".cache/jevcompass"
LOG_PATH = Path.home() / ".local/state/jevcompass/advisor.jsonl"
ROLE_CATEGORIES = {
    "explorer": ("codebase", "software"),
    "worker": ("coding", "software"),
    "luna_worker": ("operations", "general"),
}
CATALOG_TASKS = {
    "infrastructure": "ops",
    "debugging": "debug",
    "testing": "code",
    "research": "research",
    "documentation": "planning",
    "coding": "code",
    "codebase": "code",
    "operations": "ops",
    "project-setup": "project",
}
TASK_PATTERNS = (
    ("project-setup", re.compile(r"(?=.*\b(create|new|start|build|bootstrap|scaffold|setup|set up|utwór\w*|założ\w*|stworze\w*|now\w*|zbudow\w*)\b)(?=.*\b(repository|repo|repozytorium|package|pakiet|project|projekt)\b)", re.I)),
    ("infrastructure", re.compile(r"\b(kubernetes|kubectl|helm|k3s|deploy|deployment|cluster|terraform|infra|wdroż|klaster)\b", re.I)),
    ("debugging", re.compile(r"\b(debug|diagnos|bug|error|failure|regress|napraw|błąd|awari)\w*", re.I)),
    ("testing", re.compile(r"\b(test|pytest|unittest|ci|walidac|verify|weryfik)\w*", re.I)),
    ("research", re.compile(r"\b(research|porówn|analiz|źródł|search|browse|dokumentac)\w*", re.I)),
    ("documentation", re.compile(r"\b(docs|documentation|readme|instrukcj|dokument)\w*", re.I)),
    ("coding", re.compile(r"\b(code|coding|implement|refactor|python|typescript|javascript|funkcj|implementac|program)\w*", re.I)),
)
DOMAIN_PATTERNS = (
    ("kubernetes", re.compile(r"\b(kubernetes|kubectl|helm|k3s|cluster|klaster)\b", re.I)),
    ("python", re.compile(r"\b(python|pytest|django|fastapi)\b", re.I)),
    ("web", re.compile(r"\b(web|frontend|browser|react|typescript|javascript)\b", re.I)),
)


def classify_plan(prompt: str) -> tuple[str, str] | None:
    """Only emit allowlisted categories; never copy user text into Jev state."""
    if not isinstance(prompt, str) or len(prompt) > 50_000:
        return None
    task_kind = next((name for name, pattern in TASK_PATTERNS if pattern.search(prompt)), None)
    if task_kind is None:
        return None
    domain = next((name for name, pattern in DOMAIN_PATTERNS if pattern.search(prompt)), "general")
    if task_kind == "project-setup" and domain == "general":
        domain = "software"
    return task_kind, domain


def _candidate_payload(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "id": item["id"],
            "capability": item["capability"],
            "use_when": item["use_when"],
            "avoid_when": item["avoid_when"],
            "availability": item.get("availability", "available"),
        }
        for item in items
    ]


def _balanced_shortlist(items: list[dict[str, Any]], per_kind: int = 3) -> list[dict[str, Any]]:
    """Keep Jev's choice set small and balanced between tools and skills."""
    result: list[dict[str, Any]] = []
    for kind in ("tool", "skill"):
        result.extend(item for item in items if item["kind"] == kind)  # preserve curated order
        group = [item for item in result if item["kind"] == kind]
        if len(group) > per_kind:
            kept = {item["id"] for item in group[:per_kind]}
            result = [item for item in result if item["kind"] != kind or item["id"] in kept]
    return result


def _questions(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    questions: dict[str, dict[str, Any]] = {}
    for kind in ("tool", "skill"):
        group = [item for item in items if item["kind"] == kind]
        if len(group) < 2:
            continue
        questions[kind] = {
            "type": "choice",
            "instructions": f"Which catalogued {kind} should be considered first for this task category, given the use and avoid criteria?",
            "criteria": {item["id"]: item["capability"] for item in group},
        }
    return questions


def _cache_key(event: str, category: str, role: str, domain: str, items: list[dict[str, Any]]) -> str:
    material = ["decisions-v1", catalog_version(), event, category, role, domain,
                configured_model(), MIN_CONFIDENCE,
                sorted((item["id"], item.get("availability"), item.get("capability"),
                        item.get("use_when"), item.get("avoid_when")) for item in items)]
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _read_cache(key: str, allowed: set[str]) -> list[str] | None:
    try:
        data = json.loads((CACHE_DIR / (key + ".json")).read_text())
        if time.time() - data["created"] > 86_400:
            return None
        selected = data["selected"]
        if isinstance(selected, list) and all(isinstance(value, str) and value in allowed for value in selected):
            return selected
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def _write_cache(key: str, selected: list[str]) -> None:
    try:
        CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = CACHE_DIR / (key + ".json")
        temporary = CACHE_DIR / (key + f".{os.getpid()}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"created": time.time(), "selected": selected}, stream)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError:
        pass


def _judge(category: str, role: str, domain: str, items: list[dict[str, Any]]) -> list[str] | None:
    questions = _questions(items)
    if not questions:
        return None
    state = {"task_kind": category, "role": role, "domain": domain,
             "candidates": _candidate_payload(items)}
    try:
        answers = DecisionsClient(timeout=JEV_TIMEOUT).decide(state, questions)
        if set(answers) != set(questions):
            return None
        selected: list[str] = []
        for qid, answer in answers.items():
            if not isinstance(answer, dict) or answer.get("type") != "choice":
                return None
            choice = answer.get("choice")
            if not isinstance(choice, str) or choice not in questions[qid]["criteria"]:
                return None
            confidence = answer.get("confidence")
            if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                    or not MIN_CONFIDENCE <= confidence <= 1):
                continue
            selected.append(choice)
        return selected or None
    except (DecisionsError, OSError, ValueError, TypeError, KeyError):
        return None


def _context(event: str, selected: list[str], items: list[dict[str, Any]], trace: str | None = None) -> dict[str, Any] | None:
    chosen = {item["id"]: item for item in items}
    lines = []
    for identifier in selected:
        item = chosen.get(identifier)
        if item is None:
            return None
        suffix = " (configured locally; confirm it is connected in this session)" if item.get("availability") == "configured" else ""
        lines.append(f"- {item['kind']} `{identifier}`: {item['capability']}{suffix}")
    if not lines:
        return None
    if event == "UserPromptSubmit":
        prefix = "Optional planning tools and skills to verify against the task and their actual availability:\n"
        suffix = "\nConfigured MCP entries must be confirmed connected in this session. In the plan, include concise execution recommendations for the primary agent and any useful subagents. Follow all required project instructions and read a skill before using it."
    else:
        prefix = "Optional tools and skills for this agent role; validate them against your actual task and availability:\n"
        suffix = "\nConfigured MCP entries must be confirmed connected in this session. Read any chosen skill before use. Follow the task brief and required project instructions."
    context = (f"Diagnostic advice id: {trace}\n" if trace else "") + prefix + "\n".join(lines) + suffix
    if len(context) > MAX_CONTEXT_CHARS:
        return None
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}


def _metric(event: str, category: str, status: str, started: float, trace: str | None = None) -> None:
    # No prompt, model response, paths, or memory content are ever logged.
    try:
        LOG_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as stream:
            record = {"event": event, "category": category, "status": status,
                      "duration_ms": round((time.monotonic() - started) * 1000, 2)}
            if trace:
                record["trace"] = trace
            stream.write(json.dumps(record) + "\n")
    except OSError:
        pass


def select_advice(name: str, category: str, domain: str, role: str, trace: str | None = None) -> dict[str, Any] | None:
    started = time.monotonic()
    pool = candidates(task_kind=CATALOG_TASKS[category], role="any", domain=domain, limit=20)
    items = _balanced_shortlist(pool, per_kind=3)
    if not isinstance(items, list) or not _questions(items):
        _metric(name, category, "insufficient-candidates", started, trace)
        return None
    key = _cache_key(name, category, role, domain, items)
    allowed = {item["id"] for item in items}
    selected = _read_cache(key, allowed)
    status = "cache" if selected is not None else "jev"
    if selected is None:
        selected = _judge(category, role, domain, items)
        if selected:
            _write_cache(key, selected)
    output = _context(name, selected, items, trace) if selected else None
    _metric(name, category, status if output else "skip", started, trace)
    return output


def evaluate(event: dict[str, Any], trace: str | None = None) -> dict[str, Any] | None:
    name = event.get("hook_event_name")
    started = time.monotonic()
    if name == "UserPromptSubmit":
        if event.get("permission_mode") != "plan":
            if trace:
                _metric(name, "none", "mode-skip", started, trace)
            return None
        parsed = classify_plan(event.get("prompt"))
        if parsed is None:
            if trace:
                _metric(name, "none", "classification-skip", started, trace)
            return None
        category, domain = parsed
        if domain == "general" and category in {"coding", "debugging", "testing", "infrastructure"}:
            domain = "software"
        role = "planner"
    elif name == "SubagentStart":
        role = event.get("agent_type")
        if role not in ROLE_CATEGORIES:
            if trace:
                _metric(name, "none", "role-skip", started, trace)
            return None
        category, domain = ROLE_CATEGORIES[role]
    else:
        return None
    return select_advice(name, category, domain, role, trace)


def hook_main() -> int:
    """Read one Codex hook event and always exit without blocking it."""
    try:
        raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
        if len(raw) > MAX_EVENT_BYTES:
            return 0
        event = json.loads(raw)
        if isinstance(event, dict):
            diagnostic = os.environ.get("JEV_ADVISOR_DIAGNOSTIC") == "1"
            trace = secrets.token_hex(4) if diagnostic else None
            if diagnostic:
                name = event.get("hook_event_name")
                mode = event.get("permission_mode")
                collaboration = event.get("collaboration_mode")
                _metric(name if name in {"UserPromptSubmit", "SubagentStart"} else "unknown",
                        mode if mode in {"default", "plan", "acceptEdits", "dontAsk", "bypassPermissions"} else "unknown",
                        "collab-plan" if collaboration == "plan" else "collab-unavailable", time.monotonic(), trace)
            output = evaluate(event, trace)
            if output:
                print(json.dumps(output, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return 0


def hook_command() -> str:
    """Return a shell-safe hook command bound to this Python installation."""
    import shlex
    return shlex.join([sys.executable, "-m", "jevcompass", "hook"])
