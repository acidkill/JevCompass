"""Explicit, local installation of JevCompass's bundled Codex skills."""
from __future__ import annotations

from hashlib import sha256
from importlib.resources import files
import os
from pathlib import Path
import shutil
import stat
import tempfile

from .paths import resolve_codex_home


SKILL_NAMES = (
    "jevcompass-focused-tests", "jevcompass-regression-review",
    "jevcompass-plan-implementation", "jevcompass-plan-cutover",
    "jevcompass-coding-workflow",
)

# SHA-256 -> introducing Git revision for exact historical bundled bytes.
# a2148b4, ca80d9b/6d8a89a, and 5ad233d precede the current focused-tests
# contents (4f8ea2e..., introduced at 23462ad). Arbitrary edits are never accepted.
_REFRESHABLE_PRIOR_VERSIONS = {
    "jevcompass-focused-tests": {
        "a045c63304fac3ff220dd0aa69a35590807e425d5d56d5eebfdd7d60ea85b781": "a2148b4",
        "7197988e080a930fbdbe623bed506bf552d4fbc6d72b18c88fb556bd23b65b1b": "ca80d9b/6d8a89a",
        "2089cf0f10e781919ebcb09d32498e095abf751e1de029a7651593b692cc7911": "5ad233d",
    },
}
_REFRESHABLE_SHA256 = {
    name: frozenset(versions) for name, versions in _REFRESHABLE_PRIOR_VERSIONS.items()
}
_BACKUP_ROOT_NAME = ".jevcompass-bundled-skill-backups"


def skill_root() -> Path:
    """Use the current user skill root, or the isolated Codex profile when set."""
    if os.environ.get("CODEX_HOME"):
        return resolve_codex_home() / "skills"
    return Path.home() / ".agents" / "skills"


def _bundled_content(name: str) -> str:
    return files("jevcompass").joinpath("bundled_skills", name, "SKILL.md").read_text(
        encoding="utf-8"
    )


def _atomic_write(path: Path, content: bytes, *, mode: int) -> None:
    """Replace one file atomically using a temporary sibling file."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _create_new_file(path: Path, content: bytes, *, mode: int) -> None:
    """Create a file without replacing a concurrently created destination."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise

def _backup_path(root: Path, name: str, digest: str) -> Path:
    return root / _BACKUP_ROOT_NAME / name / f"{digest}.SKILL.md"


def _validate_backup_path(path: Path, previous: bytes) -> None:
    backup_root = path.parents[1]
    skill_backup_dir = path.parent
    for directory in (backup_root, skill_backup_dir):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("skill backup destination conflicts")
    if path.is_symlink():
        raise ValueError("skill backup destination conflicts")
    if path.exists() and (not path.is_file() or path.read_bytes() != previous):
        raise ValueError("skill backup destination conflicts")


def _create_backup(path: Path, previous: bytes) -> tuple[bool, list[Path]]:
    """Create a non-clobbering backup before a refresh."""
    created_directories: list[Path] = []
    try:
        for directory in (path.parents[1], path.parent):
            if not directory.exists():
                directory.mkdir(mode=0o700)
                created_directories.append(directory)
            elif directory.is_symlink() or not directory.is_dir():
                raise ValueError("skill backup destination conflicts")
        if path.exists():
            if path.is_symlink() or not path.is_file() or path.read_bytes() != previous:
                raise ValueError("skill backup destination conflicts")
            return False, created_directories
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(previous)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise
        return True, created_directories
    except BaseException:
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def _remove_empty_directories(directories: list[Path]) -> None:
    seen: set[Path] = set()
    for directory in reversed(directories):
        if directory in seen:
            continue
        seen.add(directory)
        try:
            directory.rmdir()
        except OSError:
            pass

def install_skills(*, dry_run: bool = False, root: Path | None = None,
                  refresh: bool = False) -> str:
    """Install bundled skills; refresh accepts only exact verified old versions."""
    if not isinstance(dry_run, bool) or not isinstance(refresh, bool):
        raise ValueError("invalid skill install option")
    target_root = root if root is not None else skill_root()
    if target_root.is_symlink() or (target_root.exists() and not target_root.is_dir()):
        raise ValueError("skill root is not a regular directory")
    contents = {name: _bundled_content(name).encode("utf-8") for name in SKILL_NAMES}
    pending: list[str] = []
    refreshes: list[tuple[str, bytes, int, str, Path]] = []
    unchanged = 0
    for name, content in contents.items():
        target = target_root / name
        if target.is_symlink():
            raise ValueError(f"skill destination conflicts: {name}")
        if not target.exists():
            pending.append(name)
            continue
        entrypoint = target / "SKILL.md"
        if (not target.is_dir() or entrypoint.is_symlink()
                or not entrypoint.is_file()):
            raise ValueError(f"skill destination conflicts: {name}")
        previous = entrypoint.read_bytes()
        if previous == content:
            unchanged += 1
            continue
        if not refresh:
            # Retain the original text comparison behavior unless refresh was
            # explicitly requested.
            if entrypoint.read_text(encoding="utf-8") == content.decode("utf-8"):
                unchanged += 1
                continue
            raise ValueError(f"skill destination conflicts: {name}")
        digest = sha256(previous).hexdigest()
        if digest not in _REFRESHABLE_SHA256.get(name, frozenset()):
            raise ValueError(f"skill destination conflicts: {name}")
        backup = _backup_path(target_root, name, digest)
        _validate_backup_path(backup, previous)
        refreshes.append((name, previous, stat.S_IMODE(entrypoint.stat().st_mode),
                          digest, backup))

    if dry_run:
        return (
            f"Skill install plan valid: {len(pending)} new, {len(refreshes)} refreshable, "
            f"{unchanged} unchanged; no files changed"
        )
    if not pending and not refreshes:
        return "Bundled skills already installed; no files changed"

    target_root.mkdir(parents=True, exist_ok=True)
    created_backups: list[Path] = []
    created_backup_directories: list[Path] = []
    created_skill_directories: list[Path] = []
    replaced_files: list[tuple[Path, bytes, int]] = []
    try:
        for _, previous, _, _, backup in refreshes:
            created, directories = _create_backup(backup, previous)
            created_backup_directories.extend(directories)
            if created:
                created_backups.append(backup)
        for name, previous, mode, _, _ in refreshes:
            destination = target_root / name / "SKILL.md"
            _atomic_write(destination, contents[name], mode=mode)
            replaced_files.append((destination, previous, mode))
        for name in pending:
            destination_dir = target_root / name
            destination_dir.mkdir(mode=0o700)
            created_skill_directories.append(destination_dir)
            _create_new_file(destination_dir / "SKILL.md", contents[name], mode=0o644)
    except BaseException as error:
        rollback_errors: list[BaseException] = []
        for destination, previous, mode in reversed(replaced_files):
            try:
                _atomic_write(destination, previous, mode=mode)
            except BaseException as rollback_error:
                rollback_errors.append(rollback_error)
        for destination_dir in reversed(created_skill_directories):
            try:
                shutil.rmtree(destination_dir)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            # Keep each completed backup available for manual recovery if rollback
            # could not restore all changed files.
            raise RuntimeError("skill installation failed and rollback was incomplete") from error
        for backup in reversed(created_backups):
            try:
                backup.unlink()
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        _remove_empty_directories(created_backup_directories)
        if rollback_errors:
            raise RuntimeError("skill installation failed and rollback was incomplete") from error
        raise

    if refreshes and pending:
        return (
            f"Installed {len(pending)} new and refreshed {len(refreshes)} bundled skills; "
            "start a new Codex session if they are not discovered"
        )
    if refreshes:
        return (
            f"Refreshed {len(refreshes)} bundled skills; "
            "start a new Codex session if they are not discovered"
        )
    return (
        f"Installed {len(pending)} bundled skills; "
        "start a new Codex session if they are not discovered"
    )
