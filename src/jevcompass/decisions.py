"""Small, testable client for OpenRouter's typed Decisions API."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Literal

from .credentials import resolve_api_key
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MODEL = "typesafe/jev-1.13"
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models?output_modalities=decisions&q=jev"
MAX_RESPONSE_BYTES = 128_000


class DecisionsError(Exception):
    """A remote decision could not be safely used; details never contain a key or body."""


def configured_model() -> str:
    return os.environ.get("JEVCOMPASS_MODEL", MODEL).strip()


def _post(url: str, body: bytes, api_key: str, timeout: float) -> bytes:
    request = Request(url, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }, method="POST")
    with urlopen(request, timeout=timeout) as response:
        result = response.read(MAX_RESPONSE_BYTES + 1)
    if len(result) > MAX_RESPONSE_BYTES:
        raise DecisionsError("response-too-large")
    return result


class DecisionsClient:
    """Transport boundary shared by today's advisor and future typed decisions."""

    def __init__(self, *, api_key: str | None = None, model: str | None = None,
                 timeout: float = 1.5,
                 transport: Callable[[str, bytes, str, float], bytes] = _post):
        self.api_key = api_key if api_key is not None else resolve_api_key()
        self.model = model if model is not None else configured_model()
        self.timeout = timeout
        self.transport = transport

    def decide(self, state: dict[str, Any], questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        if not self.api_key or not self.model:
            raise DecisionsError("not-configured")
        if not questions:
            raise DecisionsError("no-questions")
        body = json.dumps({"model": self.model, "state": state, "questions": questions},
                          ensure_ascii=True, separators=(",", ":")).encode()
        try:
            raw = self.transport(ENDPOINT, body, self.api_key, self.timeout)
            data = json.loads(raw)
        except HTTPError as error:
            raise DecisionsError(f"http-{error.code}") from None
        except (URLError, TimeoutError, OSError):
            raise DecisionsError("unavailable") from None
        except (ValueError, UnicodeDecodeError, TypeError):
            raise DecisionsError("invalid-json") from None
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
            raise DecisionsError("invalid-answers")
        return data["answers"]


def model_status(model: str | None = None, *, timeout: float = 2.0,
                fetch: Callable[..., Any] = urlopen) -> Literal["available", "missing", "unavailable"]:
    """Classify public model metadata without making a billed Decisions request."""
    selected = model if model is not None else configured_model()
    try:
        with fetch(MODELS_ENDPOINT, timeout=timeout) as response:
            data = json.load(response)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            return "unavailable"
        listed = any(
            entry.get("id") == selected and
            "decisions" in entry.get("architecture", {}).get("output_modalities", [])
            for entry in data.get("data", []) if isinstance(entry, dict)
        )
        return "available" if listed else "missing"
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, AttributeError):
        return "unavailable"


def model_available(model: str | None = None, *, timeout: float = 2.0,
                    fetch: Callable[..., Any] = urlopen) -> bool:
    """Return whether public metadata confirms the model; kept for compatibility."""
    return model_status(model, timeout=timeout, fetch=fetch) == "available"
