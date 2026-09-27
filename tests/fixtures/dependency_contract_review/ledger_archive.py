"""Fictional dependency v2 API; stdlib-only and deliberately keyword-only."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class Entry:
    """Context-managed value returned by the new dependency API."""

    def __init__(self, payload: Mapping[str, Any] | None) -> None:
        self.payload = payload
        self.closed = False
        self.close_count = 0

    def __enter__(self) -> "Entry":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        self.close_count += 1
        self.closed = True


class Client:
    """Local fake illustrating the v2 dependency's incompatible call shape."""

    def __init__(self, payload: Mapping[str, Any] | None) -> None:
        self.payload = payload
        self.last_arguments: dict[str, Any] | None = None
        self.last_entry: Entry | None = None

    def open_document(
        self, *, document_key: str, allow_stale: bool,
        cache_hint: Mapping[str, Any] | None,
    ) -> Entry:
        self.last_arguments = {
            "document_key": document_key,
            "allow_stale": allow_stale,
            "cache_hint": cache_hint,
        }
        self.last_entry = Entry(self.payload)
        return self.last_entry
