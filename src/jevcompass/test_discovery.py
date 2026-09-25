"""Bounded local discovery of focused Python tests from Git changes.

Discovery reads only Git path metadata and filesystem names. It never imports or
executes tests and never contacts a remote service.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path, PurePosixPath

from .test_order import TestCandidate

_MAX_CHANGED_PATHS = 256
_MAX_SCAN_ENTRIES = 4_096
_MAX_CANDIDATES = 32
_TEST_NAME = re.compile(r"^(?:test_[A-Za-z0-9_]+|[A-Za-z0-9_]+_test)\.py$")


def _git_paths(root: Path, args: list[str]) -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True,
            timeout=3, text=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [os.fsdecode(value) for value in result.stdout.split(b"\0") if value]


def _changed_paths(root: Path, limit: int) -> set[str] | None:
    staged = _git_paths(root, ["diff", "--cached", "--name-only", "-z", "--"])
    unstaged = _git_paths(root, ["diff", "--name-only", "-z", "--"])
    untracked = _git_paths(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    if staged is None or unstaged is None or untracked is None:
        return None
    paths = set(staged) | set(unstaged) | set(untracked)
    if len(paths) > limit:
        return None
    return paths


def _safe_relative_path(value: str) -> PurePosixPath | None:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts)
            or "\\" in value or "\0" in value):
        return None
    return path


def _without_symlinks(root: Path, relative: PurePosixPath) -> Path | None:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                return None
        except OSError:
            return None
    try:
        current.resolve(strict=False).relative_to(root)
    except (OSError, ValueError):
        return None
    return current


def _scan_test_files(root: Path, limit: int) -> tuple[list[PurePosixPath], bool]:
    found: list[PurePosixPath] = []
    visited = 0
    excluded = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist"}
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_dirs = []
        for name in dirs:
            child = current_path / name
            visited += 1
            if visited > limit:
                return [], False
            if name not in excluded and not child.is_symlink():
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in files:
            visited += 1
            if visited > limit:
                return [], False
            if not _TEST_NAME.fullmatch(name):
                continue
            candidate = current_path / name
            try:
                relative = PurePosixPath(candidate.relative_to(root).as_posix())
            except ValueError:
                continue
            if _without_symlinks(root, relative) is not None and candidate.is_file():
                found.append(relative)
    return found, True


def _command(root: Path, test_path: PurePosixPath) -> str | None:
    if not _TEST_NAME.fullmatch(test_path.name):
        return None
    parent = test_path.parent.as_posix()
    start = "." if parent == "." else parent
    start_path = _without_symlinks(root, test_path.parent)
    file_path = _without_symlinks(root, test_path)
    if start_path is None or file_path is None or not start_path.is_dir() or not file_path.is_file():
        return None
    return "python -m unittest discover -s {} -p {} -v".format(
        shlex.quote(start), shlex.quote(test_path.name)
    )


def _is_test_path(path: PurePosixPath) -> bool:
    return bool(_TEST_NAME.fullmatch(path.name))


def _test_kind(path: PurePosixPath) -> str:
    parts = set(path.parent.parts)
    if "e2e" in parts:
        return "e2e"
    if "integration" in parts:
        return "integration"
    if "contract" in parts:
        return "contract"
    return "unit"


def discover_test_candidates(
    repo_root: str | os.PathLike[str] = ".",
    *,
    max_changed_paths: int = _MAX_CHANGED_PATHS,
    max_scan_entries: int = _MAX_SCAN_ENTRIES,
) -> tuple[TestCandidate, ...]:
    """Return safe local candidates for changed Python files; abstain on uncertainty.

    A changed test file is proposed directly. A changed Python source file is
    associated only with unambiguous test files whose filenames follow the
    conventional test_<module>.py or <module>_test.py form, at most one per kind. Each command starts
    discovery in that test's own directory, selecting its exact filename.

    The function is local and read-only. It supplies no mandatory checks and
    does not run the returned command.
    """
    if max_changed_paths < 1 or max_scan_entries < 1:
        return ()
    try:
        root = Path(repo_root).resolve(strict=True)
        git_root_raw = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=root, check=True,
            capture_output=True, timeout=3, text=True,
        ).stdout.strip()
        git_root = Path(git_root_raw).resolve(strict=True)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ()
    if root != git_root or not root.is_dir():
        return ()

    changed = _changed_paths(root, max_changed_paths)
    if not changed:
        return ()
    safe_changed: list[PurePosixPath] = []
    for raw in changed:
        relative = _safe_relative_path(raw)
        if relative is None:
            continue
        if _without_symlinks(root, relative) is not None:
            safe_changed.append(relative)
    if not safe_changed:
        return ()

    tests, complete_scan = _scan_test_files(root, max_scan_entries)
    if not complete_scan:
        return ()
    by_name: dict[str, list[PurePosixPath]] = {}
    for path in tests:
        by_name.setdefault(path.name, []).append(path)

    matched: dict[PurePosixPath, float] = {}
    for changed_path in safe_changed:
        if changed_path.suffix != ".py":
            continue
        if _is_test_path(changed_path):
            existing = _without_symlinks(root, changed_path)
            if existing is not None and existing.is_file() and changed_path in tests:
                matched[changed_path] = 1.0
            continue
        stem = changed_path.stem
        expected_names = (f"test_{stem}.py", f"{stem}_test.py")
        matches = [candidate for name in expected_names for candidate in by_name.get(name, ())]
        kinds = [_test_kind(candidate) for candidate in matches]
        if matches and len(matches) == len(set(kinds)):
            for candidate in matches:
                matched[candidate] = max(matched.get(candidate, 0.0), 0.85)

    candidates: list[TestCandidate] = []
    for path, relevance in sorted(matched.items(), key=lambda item: item[0].as_posix()):
        command = _command(root, path)
        if command is None:
            continue
        candidates.append(TestCandidate(kind=_test_kind(path), command=command, relevance=relevance))
        if len(candidates) >= _MAX_CANDIDATES:
            break
    return tuple(candidates)
