"""Serialize ordered query pairs for the request client."""
from __future__ import annotations

from urllib.parse import quote, urlencode


def serialize_query(pairs: list[tuple[str, str]]) -> str:
    """Return query pairs in order, preserving the v1 quoting implementation."""
    return urlencode(pairs, quote_via=quote)
