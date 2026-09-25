"""Local-first ordering for explicit post-change test candidates.

Only coarse, allowlisted metadata is sent to the optional Decisions API. Test
commands, caller IDs, and relevance values remain in this process.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Literal, Mapping, Sequence

from .decisions import DecisionsClient


class TestKind(str, Enum):
    __test__ = False
    UNIT = "unit"
    INTEGRATION = "integration"
    CONTRACT = "contract"
    SMOKE = "smoke"
    E2E = "e2e"
    LINT = "lint"
    TYPECHECK = "typecheck"
    OTHER = "other"


class ChangedSurface(str, Enum):
    PYTHON = "python"
    API = "api"
    FRONTEND = "frontend"
    DATABASE = "database"
    CONFIG = "config"
    DOCS = "docs"
    MIXED = "mixed"
    UNKNOWN = "unknown"


KIND_DESCRIPTORS: dict[TestKind, str] = {
    TestKind.UNIT: "unit test suite",
    TestKind.INTEGRATION: "integration test suite",
    TestKind.CONTRACT: "contract test suite",
    TestKind.SMOKE: "smoke test suite",
    TestKind.E2E: "end-to-end test suite",
    TestKind.LINT: "lint checks",
    TestKind.TYPECHECK: "type checks",
    TestKind.OTHER: "other test suite",
}
CONFIDENCE_THRESHOLD = 0.70
NO_REMOTE_CHOICE = "no-remote-choice"
REMOTE_CHOICE = "remote-choice"


@dataclass(frozen=True)
class TestCandidate:
    """A locally executable test choice; command and caller ID never go remote."""

    __test__ = False

    kind: TestKind | str
    command: str
    candidate_id: str | None = None
    relevance: float = 0.0


@dataclass(frozen=True)
class RequiredTest:
    """A mandatory command retained in its supplied order regardless of ranking."""

    command: str
    required_id: str | None = None


@dataclass(frozen=True)
class TestOrderResult:
    ordered_candidates: tuple[TestCandidate, ...]
    required: tuple[RequiredTest, ...]
    status: Literal["remote-choice", "no-remote-choice"]

    @property
    def ordered_ids(self) -> tuple[str, ...]:
        """Return local caller IDs when present, otherwise opaque positional IDs."""
        return tuple(
            candidate.candidate_id or f"t{index}"
            for index, candidate in enumerate(self.ordered_candidates, start=1)
        )


def _enum_value(value: Any, enum_type: type[Enum], fallback: Enum) -> Enum:
    try:
        return enum_type(str(value).strip().lower())
    except (TypeError, ValueError):
        return fallback


def _candidate(item: TestCandidate | Mapping[str, Any]) -> TestCandidate:
    if isinstance(item, TestCandidate):
        kind = _enum_value(item.kind.value if isinstance(item.kind, TestKind) else item.kind,
                           TestKind, TestKind.OTHER)
        return TestCandidate(kind, str(item.command), item.candidate_id,
                             _safe_relevance(item.relevance))
    if not isinstance(item, Mapping):
        raise TypeError("invalid-test-candidate")
    kind = _enum_value(item.get("kind"), TestKind, TestKind.OTHER)
    relevance = item.get("relevance", 0.0)
    candidate_id = item.get("id", item.get("candidate_id"))
    return TestCandidate(
        kind,
        str(item.get("command", "")),
        None if candidate_id is None else str(candidate_id),
        _safe_relevance(relevance),
    )


def _required(item: RequiredTest | Mapping[str, Any]) -> RequiredTest:
    if isinstance(item, RequiredTest):
        return item
    if not isinstance(item, Mapping):
        raise TypeError("invalid-required-test")
    required_id = item.get("id", item.get("required_id"))
    return RequiredTest(str(item.get("command", "")),
                        None if required_id is None else str(required_id))


def _safe_relevance(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return score if math.isfinite(score) else 0.0


def _local_order(candidates: Sequence[TestCandidate]) -> tuple[TestCandidate, ...]:
    # Stable ranking: local relevance first, then original order for ties.
    return tuple(sorted(candidates, key=lambda candidate: -candidate.relevance))


def rank_tests(
    surface: ChangedSurface | str,
    candidates: Sequence[TestCandidate | Mapping[str, Any]],
    required: Sequence[RequiredTest | Mapping[str, Any]],
    client: Any | None = None,
) -> TestOrderResult:
    """Rank optional tests while always retaining mandatory commands unchanged.

    Remote advice is considered only when two or more distinct allowlisted test
    kinds provide meaningful alternatives. Any malformed, uncertain, unavailable,
    or incomplete answer falls back to the stable local relevance order.
    """
    normalized_surface = _enum_value(surface.value if isinstance(surface, ChangedSurface) else surface,
                                     ChangedSurface, ChangedSurface.UNKNOWN)
    normalized_candidates = tuple(_candidate(item) for item in candidates)
    local_candidates = tuple(
        candidate if candidate.candidate_id is not None else TestCandidate(
            candidate.kind, candidate.command, f"t{index}", candidate.relevance
        )
        for index, candidate in enumerate(normalized_candidates, start=1)
    )
    mandatory = tuple(_required(item) for item in required)
    fallback = _local_order(local_candidates)

    # A single Python unit check and a public contract check have a clear
    # local fast-feedback order when the unit is at least as relevant. The
    # matched coding pilot saw three remote calls choose this same order,
    # adding latency without changing the check or the mandatory suite.
    if (normalized_surface is ChangedSurface.PYTHON and len(local_candidates) == 2
            and {item.kind for item in local_candidates} == {TestKind.UNIT, TestKind.CONTRACT}):
        unit = next(item for item in local_candidates if item.kind is TestKind.UNIT)
        contract = next(item for item in local_candidates if item.kind is TestKind.CONTRACT)
        if unit.relevance >= contract.relevance:
            return TestOrderResult((unit, contract), mandatory, NO_REMOTE_CHOICE)

    # Same-kind commands are indistinguishable to the remote service; don't ask it
    # to choose among alternatives represented by identical safe metadata.
    candidate_kinds = {candidate.kind for candidate in local_candidates}
    # Clear local relevance evidence should win without a paid, slower choice.
    scores = sorted((candidate.relevance for candidate in local_candidates), reverse=True)
    if (len(local_candidates) < 2 or len(candidate_kinds) < 2
            or scores[0] - scores[1] > 0.20):
        return TestOrderResult(fallback, mandatory, NO_REMOTE_CHOICE)

    opaque_ids = {f"t{index}": candidate for index, candidate in enumerate(local_candidates, start=1)}
    criteria = {
        opaque_id: KIND_DESCRIPTORS[candidate.kind]
        for opaque_id, candidate in opaque_ids.items()
    }
    # IDs are unique, while descriptors come only from the allowlisted enum.
    state = {
        "changed_surface": normalized_surface.value,
        "candidates": [
            {"id": opaque_id, "kind": candidate.kind.value, "descriptor": KIND_DESCRIPTORS[candidate.kind]}
            for opaque_id, candidate in opaque_ids.items()
        ],
    }
    questions = {
        "first": {
            "type": "choice",
            "instructions": "Choose the test kind that should run first after the change.",
            "criteria": criteria,
        }
    }

    try:
        answers = (client if client is not None else DecisionsClient()).decide(state, questions)
        if not isinstance(answers, Mapping) or set(answers) != set(questions):
            return TestOrderResult(fallback, mandatory, NO_REMOTE_CHOICE)
        answer = answers.get("first")
        if not isinstance(answer, Mapping) or answer.get("type") != "choice":
            return TestOrderResult(fallback, mandatory, NO_REMOTE_CHOICE)
        selected_id = answer.get("choice")
        confidence = answer.get("confidence")
        if selected_id not in opaque_ids or isinstance(confidence, bool):
            return TestOrderResult(fallback, mandatory, NO_REMOTE_CHOICE)
        if not isinstance(confidence, (int, float)):
            return TestOrderResult(fallback, mandatory, NO_REMOTE_CHOICE)
        confidence_value = float(confidence)
        if not math.isfinite(confidence_value) or confidence_value < CONFIDENCE_THRESHOLD:
            return TestOrderResult(fallback, mandatory, NO_REMOTE_CHOICE)
        selected = opaque_ids[selected_id]
        ordered = (selected,) + tuple(candidate for candidate in fallback if candidate is not selected)
        return TestOrderResult(ordered, mandatory, REMOTE_CHOICE)
    except Exception:
        # Do not retain or surface transport exceptions: they can carry unsafe data.
        return TestOrderResult(fallback, mandatory, NO_REMOTE_CHOICE)
