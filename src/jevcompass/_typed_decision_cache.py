"""Opt-in private disk cache for the repository's typed decision callers.

Callers provide only their already-sanitized request for digesting and cache
only validated opaque choice tokens. The request itself is never persisted.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Iterable

_CACHE_SCOPES = frozenset({"strategy", "test_order", "triage"})
_OPAQUE_TEST_TOKEN = re.compile(r"t[1-9][0-9]{0,2}\Z")
_TOKENS_BY_SCOPE = {
    "strategy": frozenset({
        "reproduce_failure", "inspect_dependency_or_symbol_use",
        "define_contract_then_implement", "trace_data_flow",
    }),
    "triage": frozenset({
        "assertion_expectation_drift", "assertion_behavior_regression",
        "confirm_behavior_contract", "collection_syntax", "collection_import_side_effect",
        "dependency_missing", "dependency_version_conflict", "environment_config_missing",
        "environment_platform_mismatch", "import_module_missing", "import_path_changed",
        "network_service_unavailable", "network_connectivity", "permission_access_denied",
        "permission_sandbox_restriction", "timeout_contention", "timeout_nonterminating",
    }),
}
TTL_SECONDS = 86_400
MAX_ENTRIES = 128
MAX_RECORD_BYTES = 1024


@dataclass(frozen=True)
class CachedChoice:
    tokens: tuple[str, ...]
    confidence: float


class TypedDecisionCache:
    """Caller-scoped cache; disabled unless JEVCOMPASS_TYPED_DECISION_CACHE=1."""

    def __init__(self, *, scope: str, model: str, policy_version: str,
                 confidence_threshold: float, request: dict[str, Any]):
        if scope not in _CACHE_SCOPES:
            raise ValueError("unsupported typed decision cache scope")
        self._enabled = os.environ.get("JEVCOMPASS_TYPED_DECISION_CACHE") == "1"
        self._directory_override = os.environ.get("JEVCOMPASS_TYPED_CACHE_DIR")
        self._scope = scope
        self._threshold = confidence_threshold
        self._key = ""
        if not self._enabled:
            return
        material = {
            "cache_schema": "typed-choice-v1",
            "scope": scope,
            "model": model,
            "policy_version": policy_version,
            "confidence_threshold": confidence_threshold,
            "request": request,
        }
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False).encode("ascii")
        if len(canonical) > 32_768:
            raise ValueError("typed request exceeds cache digest limit")
        self._key = hashlib.sha256(canonical).hexdigest()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get(self, eligible_tokens: Iterable[str], *, allow_many: bool = False) -> CachedChoice | None:
        if not self._enabled:
            return None
        allowed = set(eligible_tokens)
        try:
            path, directory = self._record_path()
            if directory is None:
                return None
            info = path.lstat()
            if (not path.is_file() or path.is_symlink() or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_size > MAX_RECORD_BYTES):
                return None
            record = json.loads(path.read_text(encoding="utf-8"))
            if set(record) != {"created", "tokens", "confidence"}:
                return None
            created = record["created"]
            tokens = record["tokens"]
            confidence = record["confidence"]
            if (isinstance(created, bool) or not isinstance(created, (int, float))
                    or not math.isfinite(created) or time.time() - created > TTL_SECONDS
                    or created > time.time() + 60):
                return None
            if (not isinstance(tokens, list) or not tokens or len(tokens) > 8
                    or any(not self._valid_token(token) for token in tokens)
                    or len(set(tokens)) != len(tokens)
                    or (not allow_many and len(tokens) != 1)
                    or any(token not in allowed for token in tokens)):
                return None
            if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                    or not math.isfinite(confidence)
                    or not self._threshold <= confidence <= 1):
                return None
            return CachedChoice(tuple(tokens), float(confidence))
        except (OSError, ValueError, TypeError, KeyError, OverflowError, json.JSONDecodeError):
            return None

    def put(self, tokens: Iterable[str], confidence: float, *,
            eligible_tokens: Iterable[str], allow_many: bool = False) -> None:
        if not self._enabled:
            return
        selected = tuple(tokens)
        allowed = set(eligible_tokens)
        if (not selected or len(selected) > 8 or (not allow_many and len(selected) != 1)
                or len(set(selected)) != len(selected)
                or any(not self._valid_token(token) or token not in allowed
                       for token in selected)
                or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence)
                or not self._threshold <= confidence <= 1):
            return
        try:
            path, directory = self._record_path(create=True)
            if directory is None:
                return
            record = json.dumps({"created": time.time(), "tokens": list(selected),
                                 "confidence": float(confidence)},
                                sort_keys=True, separators=(",", ":")).encode("ascii")
            if len(record) > MAX_RECORD_BYTES:
                return
            fd, temporary_name = tempfile.mkstemp(prefix=".jevcompass-typed-", dir=directory)
            temporary = Path(temporary_name)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(record)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            self._prune(directory, keep=path)
        except OSError:
            return

    def _valid_token(self, token: object) -> bool:
        if not isinstance(token, str):
            return False
        if self._scope == "test_order":
            return _OPAQUE_TEST_TOKEN.fullmatch(token) is not None
        return token in _TOKENS_BY_SCOPE[self._scope]

    def _record_path(self, *, create: bool = False) -> tuple[Path, Path | None]:
        directory = (Path(self._directory_override) if self._directory_override else
                     Path.home() / ".cache" / "jevcompass" / "typed-decisions-v1")
        if create:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            info = directory.lstat()
            if (directory.is_symlink() or not directory.is_dir()
                    or info.st_uid != os.geteuid()):
                return directory / (self._key + ".json"), None
            if info.st_mode & 0o077:
                return directory / (self._key + ".json"), None
        except OSError:
            return directory / (self._key + ".json"), None
        return directory / (self._key + ".json"), directory

    @staticmethod
    def _prune(directory: Path, *, keep: Path) -> None:
        now = time.time()
        temporary_files: list[tuple[float, Path]] = []
        for path in directory.glob(".jevcompass-typed-*"):
            if not re.fullmatch(r"\.jevcompass-typed-[A-Za-z0-9_-]+", path.name):
                continue
            try:
                info = path.lstat()
                if (path.is_symlink() or not path.is_file() or info.st_uid != os.geteuid()
                        or info.st_mode & 0o077):
                    continue
                temporary_files.append((info.st_mtime, path))
            except OSError:
                continue
        temporary_files.sort(reverse=True)
        for index, (modified, path) in enumerate(temporary_files):
            if now - modified > 60 or index >= MAX_ENTRIES:
                path.unlink(missing_ok=True)

        records: list[tuple[float, Path]] = []
        for path in directory.glob("*.json"):
            if path == keep or not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                continue
            try:
                info = path.lstat()
                if (path.is_symlink() or not path.is_file() or info.st_uid != os.geteuid()
                        or info.st_mode & 0o077 or info.st_size > MAX_RECORD_BYTES):
                    path.unlink(missing_ok=True)
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                created = data.get("created") if isinstance(data, dict) else None
                if (isinstance(created, bool) or not isinstance(created, (int, float))
                        or not math.isfinite(created) or now - created > TTL_SECONDS):
                    path.unlink(missing_ok=True)
                else:
                    records.append((float(created), path))
            except (OSError, ValueError, TypeError, OverflowError):
                path.unlink(missing_ok=True)
        records.sort(reverse=True)
        for _created, path in records[MAX_ENTRIES - 1:]:
            path.unlink(missing_ok=True)
