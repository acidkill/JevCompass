"""Current wire-format implementation for the fictional ParcelCache API."""
from __future__ import annotations

import json
from typing import Any


def encode(record: dict[str, Any]) -> bytes:
    """Encode a JSON record as deterministic UTF-8 bytes."""
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


def decode(payload: bytes) -> dict[str, Any]:
    """Decode a JSON record from UTF-8 bytes."""
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("record-must-be-json-object")
    return value
