"""Curated, local-only discovery for Jev's advisory tool and skill suggestions.

Public API: ``load_catalog()`` returns curated entries with validated availability;
``candidates(task_kind, role, domain=None)`` returns a small filtered subset.
Installed skill discovery is metadata-only and never promotes an uncurated skill.
Returned data contains no filesystem paths, commands, or MCP configuration values.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from .paths import resolve_codex_home


_HERE = Path(__file__).resolve().parent
_DEFAULT_LIMITS = {"max_directories": 2000, "max_depth": 6, "max_skill_files": 2500}
_SKILL_ROOTS = (
    Path.home() / ".codex" / "skills",
    Path.home() / ".agents" / "skills",
    Path.home() / ".codex" / "plugins" / "cache",
)
_CONFIG = Path.home() / ".codex" / "config.toml"


def _skill_roots() -> tuple[Path, ...]:
    if not os.environ.get("CODEX_HOME"):
        return _SKILL_ROOTS
    home = resolve_codex_home()
    return (home / "skills", Path.home() / ".agents" / "skills", home / "plugins" / "cache")


def _codex_config_file() -> Path:
    if not os.environ.get("CODEX_HOME"):
        return _CONFIG
    return resolve_codex_home() / "config.toml"


def discover_installed_skills(limits: dict[str, int] | None = None) -> dict[str, dict[str, str]]:
    """Enumerate bounded SKILL.md metadata by plain name and verified package alias.

    Namespaced aliases require a matching plugin-cache layout, not an unrelated
    standalone skill with the same basename. Filesystem paths are never returned.
    """
    bounds = {**_DEFAULT_LIMITS, **(limits or {})}
    found: dict[str, dict[str, str]] = {}
    for root in _skill_roots():
        if not root.is_dir():
            continue
        stack: list[tuple[Path, int]] = [(root, 0)]
        dirs_seen = files_seen = 0
        while stack and dirs_seen < bounds["max_directories"] and files_seen < bounds["max_skill_files"]:
            directory, depth = stack.pop()
            dirs_seen += 1
            try:
                with os.scandir(directory) as iterator:
                    children = list(iterator)
            except OSError:
                continue
            for child in children:
                try:
                    if child.is_dir(follow_symlinks=False) and depth < bounds["max_depth"]:
                        stack.append((Path(child.path), depth + 1))
                    elif child.name == "SKILL.md" and child.is_file(follow_symlinks=False):
                        files_seen += 1
                        metadata = _skill_frontmatter(Path(child.path))
                        if metadata.get("name"):
                            short_name = _normalize(metadata["name"].split(":")[-1])
                            found.setdefault(short_name, metadata)
                            relative = Path(child.path).relative_to(root).parts
                            # <provider>/<package>/<version>/skills/<skill>/SKILL.md
                            if len(relative) >= 6 and relative[-3] == "skills":
                                package = _normalize(relative[-5])
                                if (_normalize(relative[-2]) == short_name
                                        and _normalize(metadata["name"]) in {short_name, f"{package}:{short_name}"}):
                                    found.setdefault(f"{package}:{short_name}", metadata)
                except OSError:
                    continue
    return found


def _skill_frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:12000]
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    lines = text.splitlines()[1:]
    result: dict[str, str] = {}
    for line in lines:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key.strip() in {"name", "description"}:
            result[key.strip()] = value.strip().strip("\"'")[:500]
    return result


def _normalize(value: str) -> str:
    return "-".join(value.lower().replace("_", "-").split())


def _configured_mcp_servers() -> set[str]:
    try:
        data = tomllib.loads(_codex_config_file().read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    names: set[str] = set()

    def ready_names(entries: dict[str, Any]):
        for name, spec in entries.items():
            if isinstance(spec, dict):
                if spec.get("enabled") is False:
                    continue
                bearer_name = spec.get("bearer_token_env_var")
                if isinstance(bearer_name, str) and bearer_name and not os.environ.get(bearer_name):
                    continue
            yield str(name).lower()

    # Accept common TOML layouts without ever returning configuration values.
    for key in ("mcp_servers", "mcpServers", "servers"):
        value = data.get(key)
        if isinstance(value, dict):
            names.update(ready_names(value))
    for parent in data.values():
        if isinstance(parent, dict):
            for key in ("mcp_servers", "mcpServers", "servers"):
                value = parent.get(key)
                if isinstance(value, dict):
                    names.update(ready_names(value))
    return names


def _codex_shell_available() -> bool:
    """Check Codex's built-in shell feature without probing for an OS shell."""
    config_file = _codex_config_file()
    try:
        contents = config_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        # Codex enables the built-in shell tool by default.
        return True
    except OSError:
        return False

    try:
        data = tomllib.loads(contents)
    except tomllib.TOMLDecodeError:
        return False

    features = data.get("features")
    return not (isinstance(features, dict) and features.get("shell_tool") is False)


_LOCAL_CATALOG_LIMIT = 32
_LOCAL_FIELDS = ("capability", "use_when", "avoid_when")
_LOCAL_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,.()+'-]*$")
_LOCAL_NAME = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


def _local_catalog_path() -> Path:
    return resolve_codex_home() / "jevcompass" / "catalog.json"


def _read_local_catalog() -> list[dict[str, Any]]:
    """Ignore damaged or untrusted local metadata rather than breaking hooks."""
    try:
        path = _local_catalog_path()
        if path.is_symlink() or path.stat().st_size > 32_768:
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        if not isinstance(entries, list) or len(entries) > _LOCAL_CATALOG_LIMIT:
            return []
        return entries
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def _valid_local_entry(entry: Any, installed: dict[str, dict[str, str]]) -> bool:
    if not isinstance(entry, dict) or entry.get("approved_remote_metadata") is not True:
        return False
    name = entry.get("id")
    if not isinstance(name, str) or not _LOCAL_NAME.fullmatch(name) or name not in installed:
        return False
    if not all(
        isinstance(entry.get(field), str) and 8 <= len(entry[field]) <= 160
        and _LOCAL_TEXT.fullmatch(entry[field]) for field in _LOCAL_FIELDS
    ):
        return False
    return (
        entry.get("role") == "guidance"
        and entry.get("cost") == "low"
        and isinstance(entry.get("task_kinds"), list)
        and 1 <= len(entry["task_kinds"]) <= 4
        and all(isinstance(task, str) and task in _ALLOWED_TASKS for task in entry["task_kinds"])
        and isinstance(entry.get("domains"), list)
        and 1 <= len(entry["domains"]) <= 3
        and all(isinstance(domain, str) and domain in _ALLOWED_DOMAINS for domain in entry["domains"])
    )


_ALLOWED_TASKS = frozenset({
    "ops", "debug", "testing", "research", "api-design", "document", "code",
    "codebase", "history", "review", "source-review", "planning", "project", "package-docs",
})
_ALLOWED_DOMAINS = frozenset({
    "general", "software", "python", "web", "shell", "kubernetes", "codex", "security",
})


def register_local_skill(
    name: str, capability: str, use_when: str, avoid_when: str,
    task_kinds: list[str], domains: list[str], role: str = "guidance",
) -> dict[str, Any]:
    """Register explicitly approved generic metadata for an installed local skill.

    Fields in this returned preview may be sent to OpenRouter during remote
    selection. The SKILL.md contents and its path are never copied or submitted.
    """
    installed = discover_installed_skills()
    entry = {
        "id": name, "capability": capability, "use_when": use_when,
        "avoid_when": avoid_when, "task_kinds": task_kinds, "domains": domains,
        "role": role, "cost": "low", "approved_remote_metadata": True,
    }
    if not _valid_local_entry(entry, installed):
        raise ValueError("Skill must be installed and metadata must be short, generic, and allowlisted")
    bundled = json.loads((_HERE / "catalog_data.json").read_text(encoding="utf-8"))["entries"]
    if any(item["id"] == name for item in bundled):
        raise ValueError("A bundled catalog entry already uses this identifier")
    path = _local_catalog_path()
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if (path.is_symlink() or path.stat().st_size > 32_768
                    or not isinstance(current, dict) or current.get("schema_version") != 1
                    or not isinstance(current.get("entries"), list)
                    or len(current["entries"]) > _LOCAL_CATALOG_LIMIT
                    or any(not _valid_local_entry(item, installed) for item in current["entries"])):
                raise ValueError("Invalid local catalog")
        except (OSError, ValueError, TypeError) as error:
            raise ValueError("Existing local catalog is invalid; repair it before registering a skill") from error
    existing = _read_local_catalog()
    if any(isinstance(item, dict) and item.get("id") == name for item in existing):
        raise ValueError("This skill is already registered")
    if len(existing) >= _LOCAL_CATALOG_LIMIT:
        raise ValueError("Local catalog is full")
    path = _local_catalog_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".catalog-", delete=False) as stream:
        temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        json.dump({"schema_version": 1, "entries": [*existing, entry]}, stream, indent=2)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return entry


def catalog_snapshot() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return the curated catalog plus privacy-safe local availability counts."""
    raw = json.loads((_HERE / "catalog_data.json").read_text(encoding="utf-8"))
    skills = discover_installed_skills(raw.get("discovery_limits"))
    servers = _configured_mcp_servers()
    entries = raw.get("entries", [])
    clean: list[dict[str, Any]] = []
    for entry in entries:
        item = dict(entry)
        spec = item.pop("availability", {})
        kind = spec.get("kind")
        item["kind"] = "skill" if kind == "skill" else "tool"
        if kind == "skill":
            key = _normalize(spec.get("name", ""))
            required_mcp = item.get("requires_mcp", [])
            if key not in skills or not isinstance(required_mcp, list) or not all(
                isinstance(server, str) and server for server in required_mcp
            ):
                item["availability"] = "unavailable"
            elif required_mcp:
                # A configured server does not prove it is callable in this session.
                item["availability"] = (
                    "configured" if all(server.lower() in servers for server in required_mcp)
                    else "unavailable"
                )
            else:
                item["availability"] = "available"
        elif kind == "command":
            item["invocation"] = "shell_command"
            item["availability"] = "available" if shutil.which(spec.get("command", "")) else "unavailable"
        elif kind == "codex_shell":
            item["availability"] = "available" if _codex_shell_available() else "unavailable"
        elif kind == "mcp":
            item["availability"] = "configured" if spec.get("server", "").lower() in servers else "unavailable"
        else:
            item["availability"] = "unavailable"
        clean.append(item)

    known = {item["id"] for item in clean}
    local_entries: list[dict[str, Any]] = []
    for entry in _read_local_catalog():
        if not _valid_local_entry(entry, skills) or entry["id"] in known:
            continue
        known.add(entry["id"])
        local_entries.append({
            key: entry[key] for key in
            ("id", "capability", "use_when", "avoid_when", "role", "cost", "task_kinds", "domains")
        } | {"kind": "skill", "privacy": "approved generic metadata", "availability": "available"})
    clean = local_entries + clean  # Respect an explicit user-curated match within the shortlist limit.

    summary = {
        "curated_entries": len(clean),
        "available_tools": sum(item["kind"] == "tool" and item["availability"] == "available" for item in clean),
        "available_skills": sum(item["kind"] == "skill" and item["availability"] == "available" for item in clean),
        "configured_mcp_servers": len(servers),
        "discovered_skills": sum(":" not in name for name in skills),
        "unavailable_entries": sum(item["availability"] == "unavailable" for item in clean),
    }
    return clean, summary


def load_catalog() -> list[dict[str, Any]]:
    """Load curated entries and replace availability with a local validation result."""
    entries, _ = catalog_snapshot()
    return entries


def catalog_version() -> str:
    """Return a stable content hash for the curated catalog data."""
    data = (_HERE / "catalog_data.json").read_bytes()
    approved = [item for item in _read_local_catalog() if isinstance(item, dict)]
    canonical = json.dumps(approved, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(data + b"\0" + canonical).hexdigest()
    return f"sha256:{digest}"


def _inside_git_checkout(start: Path | None = None) -> bool:
    """Ask Git locally; a stray parent `.git` marker is not a valid checkout."""
    try:
        location = (start or Path.cwd()).resolve()
        result = subprocess.run(
            ["git", "-C", str(location), "rev-parse", "--is-inside-work-tree"],
            check=False, capture_output=True, text=True, timeout=0.25,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (OSError, subprocess.TimeoutExpired):
        return False


def candidates(task_kind: str, role: str, domain: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
    """Return up to ``limit`` available curated choices matching task and role.

    ``role`` may be an exact role or ``any``. Matching is case-insensitive.
    A candidate tagged ``general`` also matches a requested specific domain.
    """
    if limit <= 0:
        return []
    task = _normalize(task_kind)
    requested_role = _normalize(role)
    requested_domain = _normalize(domain) if domain else None
    result = []
    for entry in load_catalog():
        if entry["availability"] not in {"available", "configured"}:
            continue
        if task not in {_normalize(item) for item in entry.get("task_kinds", [])}:
            continue
        if entry["id"] == "git" and not _inside_git_checkout():
            continue
        if requested_role not in {"any", "*"} and _normalize(entry.get("role", "")) != requested_role:
            continue
        domains = {_normalize(item) for item in entry.get("domains", [])}
        inherited_domain = {"python": "software", "web": "software"}.get(requested_domain)
        if requested_domain and requested_domain not in domains and "general" not in domains and inherited_domain not in domains:
            continue
        candidate = dict(entry)
        candidate["catalog_version"] = catalog_version()
        for field in ("use_when", "avoid_when"):
            if isinstance(candidate.get(field), list):
                candidate[field] = "; ".join(candidate[field])
        result.append(candidate)
        if len(result) >= limit:
            break
    return result
