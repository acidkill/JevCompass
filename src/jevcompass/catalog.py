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
import shutil
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
    """Enumerate bounded SKILL.md metadata, keyed by normalized display name.

    Only frontmatter name/description is retained. Symlinks are skipped,
    traversal is bounded per root, and filesystem paths are never returned.
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
                            found.setdefault(_normalize(metadata["name"]), metadata)
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
            key = _normalize(spec.get("name", "").split(":")[-1])
            item["availability"] = "available" if key in skills else "unavailable"
        elif kind == "command":
            item["availability"] = "available" if shutil.which(spec.get("command", "")) else "unavailable"
        elif kind == "codex_shell":
            item["availability"] = "available" if _codex_shell_available() else "unavailable"
        elif kind == "mcp":
            item["availability"] = "configured" if spec.get("server", "").lower() in servers else "unavailable"
        else:
            item["availability"] = "unavailable"
        clean.append(item)

    summary = {
        "curated_entries": len(clean),
        "available_tools": sum(item["kind"] == "tool" and item["availability"] == "available" for item in clean),
        "available_skills": sum(item["kind"] == "skill" and item["availability"] == "available" for item in clean),
        "configured_mcp_servers": len(servers),
        "discovered_skills": len(skills),
        "unavailable_entries": sum(item["availability"] == "unavailable" for item in clean),
    }
    return clean, summary


def load_catalog() -> list[dict[str, Any]]:
    """Load curated entries and replace availability with a local validation result."""
    entries, _ = catalog_snapshot()
    return entries


def catalog_version() -> str:
    """Return a stable content hash for the curated catalog data."""
    digest = hashlib.sha256((_HERE / "catalog_data.json").read_bytes()).hexdigest()
    return f"sha256:{digest}"


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
