"""Explicit, local installation of JevCompass's bundled Codex skills."""
from __future__ import annotations

from importlib.resources import files
import os
from pathlib import Path
import shutil

from .paths import resolve_codex_home


SKILL_NAMES = ("jevcompass-focused-tests", "jevcompass-regression-review")


def skill_root() -> Path:
    """Use the current user skill root, or the isolated Codex profile when set."""
    if os.environ.get("CODEX_HOME"):
        return resolve_codex_home() / "skills"
    return Path.home() / ".agents" / "skills"


def _bundled_content(name: str) -> str:
    return files("jevcompass").joinpath("bundled_skills", name, "SKILL.md").read_text(
        encoding="utf-8"
    )


def install_skills(*, dry_run: bool = False, root: Path | None = None) -> str:
    """Install only named bundled skills, refusing collisions with user content."""
    target_root = root if root is not None else skill_root()
    if target_root.is_symlink() or (target_root.exists() and not target_root.is_dir()):
        raise ValueError("skill root is not a regular directory")
    contents = {name: _bundled_content(name) for name in SKILL_NAMES}
    pending: list[str] = []
    for name, content in contents.items():
        target = target_root / name
        if target.is_symlink():
            raise ValueError(f"skill destination conflicts: {name}")
        if target.exists():
            entrypoint = target / "SKILL.md"
            if (not target.is_dir() or entrypoint.is_symlink()
                    or not entrypoint.is_file()
                    or entrypoint.read_text(encoding="utf-8") != content):
                raise ValueError(f"skill destination conflicts: {name}")
        else:
            pending.append(name)
    if dry_run:
        return f"Skill install plan valid: {len(pending)} new, {len(SKILL_NAMES) - len(pending)} unchanged; no files changed"
    if not pending:
        return "Bundled skills already installed; no files changed"

    target_root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for name in pending:
            target = target_root / name
            target.mkdir(mode=0o700)
            created.append(target)
            with (target / "SKILL.md").open("x", encoding="utf-8") as stream:
                stream.write(contents[name])
    except (OSError, UnicodeError):
        for target in reversed(created):
            shutil.rmtree(target)
        raise
    return f"Installed {len(pending)} bundled skills; start a new Codex session if they are not discovered"
