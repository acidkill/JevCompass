"""Fictional retry helper; review it against RETRY_CONTRACT.md."""
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Response:
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)


def request_with_retry(
    send: Callable[[], Response],
    sleep: Callable[[float], None],
    max_attempts: int = 3,
    base_delay: float = 0.25,
) -> Response:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    for attempt in range(max_attempts):
        try:
            response = send()
        except Exception:
            if attempt + 1 == max_attempts:
                raise
            sleep(base_delay)
            continue
        if response.status_code == 429 or response.status_code >= 500:
            sleep(base_delay)
            if attempt + 1 == max_attempts:
                return response
            continue
        return response
    raise AssertionError("unreachable")
