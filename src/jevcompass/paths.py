"""Path helpers for the active Codex user configuration."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def resolve_codex_home() -> Path:
    """Return CODEX_HOME when set, otherwise Codex's default user config directory."""
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def codex_profile_id() -> str:
    """Identify the local Codex profile without recording its path."""
    resolved = resolve_codex_home().resolve(strict=False)
    return hashlib.sha256(os.fsencode(resolved)).hexdigest()[:16]
