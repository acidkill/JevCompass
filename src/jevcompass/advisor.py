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

from .paths import codex_profile_id

def __getattr__(name: str) -> Any:
    """Load catalog and decision helpers only when an event needs advice."""
    if name in {"candidates", "catalog_version"}:
        from importlib import import_module

        module = import_module(".catalog", __package__)
    elif name in {"DecisionsClient", "DecisionsError", "configured_model"}:
        from importlib import import_module

        module = import_module(".decisions", __package__)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value


MAX_EVENT_BYTES = 256_000
MAX_CONTEXT_CHARS = 1_500
JEV_TIMEOUT = 1.5
MIN_CONFIDENCE = 0.5
CACHE_DIR = Path.home() / ".cache/jevcompass"
LOG_PATH = Path.home() / ".local/state/jevcompass/advisor.jsonl"
ROLE_CATEGORIES = {
    "explorer": ("codebase", "software"),
    "worker": ("coding", "software"),
}
SPAWN_TOOL_NAMES = frozenset({"Agent", "spawn_agent", "collaborationspawn_agent"})
SPAWN_TEXT_LIMIT = 10_000
CATALOG_TASKS = {
    "infrastructure": "ops",
    "debugging": "debug",
    "testing": "testing",
    "research": "research",
    "api-design": "api-design",
    "documentation": "document",
    "codex-setup": "document",
    "coding": "code",
    "codebase": "codebase",
    "history": "history",
    "review": "review",
    "source-review": "source-review",
    "planning": "planning",
    "operations": "ops",
    "project-setup": "project",
    "package-docs": "package-docs",
}
TASK_PATTERNS = (
    # Setup of Codex itself benefits from official product guidance; package setup does not.
    ("codex-setup", re.compile(r"(?=.*\bcodex(?:'s)?\s+(?:desktop|cli|hooks?|settings|skills?)\b)(?=.*\b(?:hooks?|settings|skills?|configuration)\b)(?=.*\b(?:configur\w*|set\s+up|setup\s+codex|install\w*|skonfigur\w*|ustaw\w*)\b)", re.I)),
    ("package-docs", re.compile(r"(?=.*\b(?:python|pyproject\.toml|pipx?)\b)(?=.*\b(?:readme|documentation|docs)\b)(?=.*\b(?:install(?:ation)?|installing)\b)", re.I)),
    # A project mentioned as the location of a feature/test is not a new project.
    ("project-setup", re.compile(r"\b(?:creat\w*|start\w*|bootstrap\w*|scaffold\w*|setup|set up|initialize\w*|initialise\w*|init|utwórz|założ\w*|stwórz|stworze\w*|zainicjaliz\w*)\b(?:(?!\b(?:tests?|features?|functions?|files?|scripts?|docs?|documentation|modules?|components?)\b)[\s\S]){0,80}?\b(?:repository|repo|repozytorium|package|pakiet|project|projekt)\b", re.I)),
    ("api-design", re.compile(r"(?=.*\b(?:api|endpoint|openapi|rest|graphql)\b)(?:(?=.*\b(?:design\w*|architect\w*|defin\w*|specif\w*|zaprojekt\w*|projektow\w*)\b)|(?=.*\b(?:review|audit)\b)(?=.*\b(?:contract|schema|specification)\b))", re.I)),
    ("infrastructure", re.compile(r"\b(kubernetes|kubectl|helm|k3s|deploy|deployment|cluster|terraform|infra|wdroż|klaster)\b", re.I)),
    ("source-review", re.compile(r"^(?!.*\b(?:code|diff|pull request|implementation|api|readme)\b)(?=.*\b(?:review|audit|przegląd|przejrz|audyt)\w*\b)(?=.*\b(?:proposal|offer|(?:price|pricing|client|commercial)\s+quote|bid|draft|invoice|ofert|propozycj|wycen)\w*\b)", re.I)),
    ("history", re.compile(r"(?=.*\b(?:git|commit|branch|repository|repo|code|source|function|file|module|repozytorium|kod|plik|funkcj|moduł)\w*\b)(?=.*(?:\bhistory\b|\bhistori\w*|\bprovenance\b|\bblame\b|\bintroduced\b|\bauthored\b|\bwho\s+(?:changed|introduced|authored)\b|\bkto\s+(?:zmienił|wprowadził)\b))", re.I)),
    ("review", re.compile(r"\b(review|audit|diff|pull request|pr|przegląd|audyt)\b", re.I)),
    ("debugging", re.compile(r"\b(debug|diagnos|bug|error|failure|regress|defect|napraw|błąd|awari)\w*|\bfix(?:es|ed|ing)?\b", re.I)),
    ("planning", re.compile(r"\b(roadmap|task list|task breakdown|list of tasks|prepare.{0,60}task|list[ęa]\s+(?:tasków|taskow|zadań|zadan)|zaplanuj|przygotuj.{0,60}(?:task|zadani|plan)|opracuj\s+plan|priorytetyz\w*|plan\s+(?:a|an|the|this|how|for|to)\b|implementation plan|planowanie|planowania)\b", re.I)),
    ("codebase", re.compile(r"\b(inspect|understand|explain|trace|investigat|how does|what does|przejrz|zrozum|wyjaśn)\w*", re.I)),
    ("coding", re.compile(r"\b(implement|refactor|modify|zimplement|modyfik\w*)\w*", re.I)),
    ("documentation", re.compile(r"\b(docs|documentation|readme|instrukcj|dokument)\w*", re.I)),
    ("testing", re.compile(r"\b(test|pytest|unittest|ci|walidac|verify|weryfik)\w*", re.I)),
    ("research", re.compile(r"\b(research|porówn|analiz|źródeł|źródł|search|browse)\w*", re.I)),
    ("coding", re.compile(r"\b(write|add|code|coding|python|typescript|javascript|funkcj|implementac|program|dodaj|napisz)\w*", re.I)),
)
DOMAIN_PATTERNS = (
    ("kubernetes", re.compile(r"\b(kubernetes|kubectl|helm|k3s|cluster|klaster)\b", re.I)),
    ("codex", re.compile(r"\b(?:codex(?:'s)?\s+(?:desktop|cli|hooks?|settings|skills?|models?|setup|configuration|troubleshooting|customization)|(?:desktop|cli|hooks?|settings|skills?|models?|setup|configuration|troubleshooting|customization)\s+(?:in|for|with|on)\s+codex)\b", re.I)),
    ("python", re.compile(r"\b(python|pytest|django|fastapi)\b|\.py\b", re.I)),
    ("web", re.compile(r"\b(web|frontend|browser|react|typescript|javascript)\b", re.I)),
    ("shell", re.compile(r"\b(bash|shell)\b", re.I)),
)
SECURITY_INTENT = re.compile(
    r"\b(?:secur\w*|auth(?:entication|orization)?\b|permission\w*|signature\w*|signed\b|secret\w*|credential\w*|replay\b|csrf\b|injection\b|vulnerab\w*|bezpiecze\w*|uprawnieni\w*|uwierzytel\w*|podpis\w*)",
    re.I,
)
MIN_TASK_CHARS = 20
SIMPLE_REQUEST = re.compile(
    r"^\s*(?:run|execute|show|list|find|search|grep|report|check|display|explain|describe|inspect|investigate|read|change|update|edit|adjust|modify|rename|uruchom|pokaż|znajdź|sprawdź|wyjaśnij|opisz|przejrzyj|zmień|zaktualizuj|popraw)\b",
    re.I,
)
MULTI_STEP = re.compile(r"\b(?:and|then|plan|design|compare|improve|fix|implement|build|adapt|optimi[sz]\w*|error|failure|fail|bug|debug|diagnos\w*|audit|review|diff|pull request|oraz|następnie|zaplanuj|napraw|wdroż|dostosuj|usprawnij|ulepsz|zaplanuj|zmień|napraw)\b", re.I)


def classify_task(prompt: str) -> tuple[str, str] | None:
    """Classify substantive task intent locally without inferring the Codex UI mode."""
    if not isinstance(prompt, str) or not MIN_TASK_CHARS <= len(prompt) <= 50_000:
        return None
    if len(prompt) <= 100 and SIMPLE_REQUEST.search(prompt) and not MULTI_STEP.search(prompt):
        return None
    task_kind = next((name for name, pattern in TASK_PATTERNS if pattern.search(prompt)), None)
    if task_kind is None:
        return None
    domain = next((name for name, pattern in DOMAIN_PATTERNS if pattern.search(prompt)), "general")
    if domain == "codex" and task_kind not in {"documentation", "debugging", "codex-setup"}:
        domain = next((name for name, pattern in DOMAIN_PATTERNS if name != "codex" and pattern.search(prompt)), "general")
    if task_kind in {"project-setup", "api-design", "history"} and domain == "general":
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


def _context(
    event: str,
    selected: list[str],
    items: list[dict[str, Any]],
    trace: str | None = None,
    selection_source: str = "jev",
    category: str | None = None,
) -> dict[str, Any] | None:
    chosen = {item["id"]: item for item in items}
    lines = []
    has_criteria = False
    for identifier in selected:
        item = chosen.get(identifier)
        if item is None:
            return None
        suffix = " (configured locally; confirm it is connected in this session)" if item.get("availability") == "configured" else ""
        if item.get("invocation") == "shell_command":
            lines.append(f"- local command `{identifier}`: {item['capability']} (run through `exec_command`; confirm it is available in this session){suffix}")
        else:
            criteria = ""
            if item["kind"] == "skill":
                use_when, avoid_when = item.get("use_when"), item.get("avoid_when")
                if (isinstance(use_when, str) and isinstance(avoid_when, str)
                        and len(use_when) <= 160 and len(avoid_when) <= 160):
                    criteria = f" (use when: {use_when}; skip when: {avoid_when})"
                    has_criteria = True
            lines.append(f"- {item['kind']} `{identifier}`: {item['capability']}{criteria}{suffix}")
    if not lines:
        return None

    source_note = (
        "Local unranked fallback; Jev did not select these candidates. "
        if selection_source == "local" else ""
    )
    if event == "UserPromptSubmit":
        prefix = source_note + "Optional tools and skills for this task; validate against the task and actual availability:\n"
        suffix = "\nConfigured MCP entries must be confirmed connected in this session. Inspect task scope first. If a listed skill's use condition fits, read its SKILL.md before drafting or editing; otherwise skip it. Follow required project instructions and tests. If you write a plan, include concise execution recommendations for the primary agent and useful subagents."
    else:
        prefix = source_note + "Optional tools and skills for this agent role; validate them against your actual task and availability:\n"
        suffix = "\nConfigured MCP entries must be confirmed connected in this session. Inspect task scope first. Read a chosen skill only when its use condition fits. Follow the task brief and required project instructions."
    if category == "source-review":
        suffix += " Verify current authoritative local sources and mark missing facts. Keep drafts unsent unless explicitly authorized."
    if category == "package-docs":
        suffix += " For Python package install docs, check project metadata and the existing README. Preserve its exact supported CLI help invocation (such as `python -m package --help`) unless a replacement is verified by running it. Check the documented test command; distinguish pre-existing test failures."
    context = (f"JevCompass advice ID: {trace}\n" if trace else "") + prefix + "\n".join(lines) + suffix
    if len(context) > MAX_CONTEXT_CHARS:
        if has_criteria:
            compact = [{**item, "use_when": None, "avoid_when": None} for item in items]
            return _context(event, selected, compact, trace, selection_source, category)
        return None
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}


def _metric(event: str, category: str, status: str, started: float, trace: str | None = None) -> None:
    # No prompt, model response, paths, or memory content are ever logged.
    try:
        LOG_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as stream:
            record = {"event": event, "category": category, "status": status,
                      "profile": codex_profile_id(),
                      "duration_ms": round((time.monotonic() - started) * 1000, 2)}
            if trace:
                record["trace"] = trace
            stream.write(json.dumps(record) + "\n")
    except OSError:
        pass


def _load_advice_dependencies() -> None:
    """Bind catalog and decision helpers only after local eligibility succeeds."""
    if "candidates" not in globals() or "catalog_version" not in globals():
        from .catalog import candidates, catalog_version

        globals().setdefault("candidates", candidates)
        globals().setdefault("catalog_version", catalog_version)
    if not {"DecisionsClient", "DecisionsError", "configured_model"}.issubset(globals()):
        from .decisions import DecisionsClient, DecisionsError, configured_model

        globals().setdefault("DecisionsClient", DecisionsClient)
        globals().setdefault("DecisionsError", DecisionsError)
        globals().setdefault("configured_model", configured_model)


def select_advice(
    name: str, category: str, domain: str, role: str, trace: str | None = None,
    security_relevant: bool = False,
) -> dict[str, Any] | None:
    _load_advice_dependencies()
    started = time.monotonic()
    pool = candidates(task_kind=CATALOG_TASKS[category], role="any", domain=domain, limit=20)
    # Security guidance requires an explicit local task signal. A broad software
    # domain match alone cannot make it relevant to an ordinary code review.
    if not security_relevant and domain != "security":
        pool = [item for item in pool if item["id"] != "security-requirement-extraction"]
    if category == "codex-setup":
        # Office-document tooling is a broad `document` match, not Codex setup guidance.
        pool = [item for item in pool if item["id"] == "openai-docs"]
    # A configured MCP server is not proof that this Codex session exposes it.
    items = _balanced_shortlist([item for item in pool if item.get("availability") != "configured"], per_kind=3)
    if not isinstance(items, list) or not items:
        _metric(name, category, "insufficient-candidates", started, trace)
        return None
    # The built-in shell is a generic capability, not a meaningful singleton
    # recommendation for either a task hook or a role-only subagent hook.
    if len(items) == 1 and items[0]["id"] == "exec_command":
        _metric(name, category, "low-signal-skip", started, trace)
        return None
    # A spawn-time hint containing only the generic shell and Git consumes the
    # child's attention without narrowing its task. Wait for a specific skill,
    # MCP tool, or test runner; other hook events retain their existing policy.
    if name == "PreToolUse" and all(item["id"] in {"exec_command", "git"} for item in items):
        _metric(name, category, "low-signal-skip", started, trace)
        return None

    questions = _questions(items)
    singleton_ids = [
        group[0]["id"]
        for kind in ("tool", "skill")
        if len(group := [item for item in items if item["kind"] == kind]) == 1
    ]
    if not questions:
        output = _context(name, singleton_ids, items, trace, selection_source="local", category=category) if singleton_ids else None
        _metric(name, category, "local" if output else "insufficient-candidates", started, trace)
        return output

    key = _cache_key(name, category, role, domain, items)
    allowed = {item["id"] for item in items}
    choices = _read_cache(key, allowed)
    status = "cache" if choices is not None else "jev"
    if choices is None:
        choices = _judge(category, role, domain, items)
        if choices:
            _write_cache(key, choices)
        else:
            status = "local"

    if status == "local":
        # If Jev provides no usable choice, show a bounded, explicitly unranked
        # catalog shortlist instead of implying the model chose a winner.
        selected = [item["id"] for item in items]
    else:
        selected = list(dict.fromkeys([*singleton_ids, *(choices or [])]))

    source = "local" if status == "local" else "jev"
    output = _context(name, selected, items, trace, selection_source=source, category=category) if selected else None
    _metric(name, category, status if output else "skip", started, trace)
    return output


def _spawn_intent(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """Derive only bounded category/domain/role from a pending subagent call locally."""
    if event.get("tool_name") not in SPAWN_TOOL_NAMES:
        return None
    arguments = event.get("tool_input")
    if not isinstance(arguments, dict):
        return None
    message = arguments.get("message")
    title = arguments.get("task_name")
    texts: list[str] = []
    # Current Codex hosts can encode the child message as a single opaque token.
    # Natural-language messages have separated words; never decode or log the token.
    if (isinstance(message, str) and MIN_TASK_CHARS <= len(message) <= SPAWN_TEXT_LIMIT
            and re.search(r"\s", message) and len(re.findall(r"\b\w+\b", message)) >= 5):
        texts.append(message)
    if isinstance(title, str) and MIN_TASK_CHARS <= len(title) <= 160:
        normalized = re.sub(r"[_-]+", " ", title)
        if len(re.findall(r"\b\w+\b", normalized)) >= 2:
            texts.append(normalized)
    parsed = next((result for text in texts if (result := classify_task(text)) is not None), None)
    if parsed is None:
        return None
    category, domain = parsed
    if domain == "general" and category in {"coding", "debugging", "testing", "infrastructure", "codebase"}:
        domain = "software"
    role = arguments.get("agent_type")
    return category, domain, role if role in {"explorer", "worker"} else "subagent"


def evaluate(event: dict[str, Any], trace: str | None = None) -> dict[str, Any] | None:
    name = event.get("hook_event_name")
    started = time.monotonic()
    security_relevant = False
    if name == "UserPromptSubmit":
        prompt = event.get("prompt")
        parsed = classify_task(prompt)
        security_relevant = isinstance(prompt, str) and bool(SECURITY_INTENT.search(prompt))
        if parsed is None:
            if trace:
                _metric(name, "none", "classification-skip", started, trace)
            return None
        category, domain = parsed
        if domain == "general" and category in {"coding", "debugging", "testing", "infrastructure", "codebase"}:
            domain = "software"
        role = "primary"
    elif name == "SubagentStart":
        role = event.get("agent_type")
        if role not in ROLE_CATEGORIES:
            if trace:
                _metric(name, "none", "role-skip", started, trace)
            return None
        category, domain = ROLE_CATEGORIES[role]
    elif name == "PreToolUse":
        intent = _spawn_intent(event)
        if intent is None:
            if trace:
                _metric(name, "none", "classification-skip", started, trace)
            return None
        category, domain, role = intent
        arguments = event.get("tool_input", {})
        if isinstance(arguments, dict):
            # The child message may be an opaque host token. Only descriptive
            # text can supply an extra local signal; neither is sent to Jev.
            security_relevant = any(
                isinstance(value, str) and bool(SECURITY_INTENT.search(value))
                for value in (arguments.get("task_name"), arguments.get("message"))
            )
    else:
        return None
    return select_advice(name, category, domain, role, trace, security_relevant=security_relevant)


def hook_main() -> int:
    """Read one Codex hook event and always exit without blocking it."""
    try:
        raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
        if len(raw) > MAX_EVENT_BYTES:
            return 0
        event = json.loads(raw)
        if isinstance(event, dict):
            diagnostic = os.environ.get("JEV_ADVISOR_DIAGNOSTIC") == "1"
            name = event.get("hook_event_name")
            eligible = ((name == "UserPromptSubmit" and classify_task(event.get("prompt")) is not None)
                        or (name == "SubagentStart" and event.get("agent_type") in ROLE_CATEGORIES)
                        or (name == "PreToolUse" and _spawn_intent(event) is not None))
            trace = secrets.token_hex(4) if eligible or diagnostic else None
            if diagnostic:
                mode = event.get("permission_mode")
                collaboration = event.get("collaboration_mode")
                _metric(name if name in {"UserPromptSubmit", "SubagentStart", "PreToolUse"} else "unknown",
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
