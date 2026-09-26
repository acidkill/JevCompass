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


class ChangeSignal(str, Enum):
    PUBLIC_CONTRACT_CHANGED = "public_contract_changed"
    BOUNDARY_MAPPING_CHANGED = "boundary_mapping_changed"
    INTERNAL_LOGIC_CHANGED = "internal_logic_changed"


class Coverage(str, Enum):
    DIRECT = "direct"
    INDIRECT = "indirect"
    UNKNOWN = "unknown"


class RuntimeBucket(str, Enum):
    FAST = "fast"
    SLOW = "slow"
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
    """A local test choice; command, caller ID, and relevance never go remote."""

    __test__ = False

    kind: TestKind | str
    command: str
    candidate_id: str | None = None
    relevance: float = 0.0
    coverage: Coverage | str = Coverage.UNKNOWN
    runtime: RuntimeBucket | str = RuntimeBucket.UNKNOWN


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


def _enum_value(value: Any, enum_type: type[Enum], fallback: Enum | None) -> Enum | None:
    if isinstance(value, Enum):
        value = value.value
    try:
        return enum_type(str(value).strip().lower())
    except (TypeError, ValueError):
        return fallback


def _candidate(item: TestCandidate | Mapping[str, Any]) -> TestCandidate:
    if isinstance(item, TestCandidate):
        kind = _enum_value(item.kind.value if isinstance(item.kind, TestKind) else item.kind,
                           TestKind, TestKind.OTHER)
        coverage = _enum_value(item.coverage, Coverage, Coverage.UNKNOWN)
        runtime = _enum_value(item.runtime, RuntimeBucket, RuntimeBucket.UNKNOWN)
        return TestCandidate(kind, str(item.command), item.candidate_id,
                             _safe_relevance(item.relevance), coverage, runtime)
    if not isinstance(item, Mapping):
        raise TypeError("invalid-test-candidate")
    kind = _enum_value(item.get("kind"), TestKind, TestKind.OTHER)
    coverage = _enum_value(item.get("coverage"), Coverage, Coverage.UNKNOWN)
    runtime = _enum_value(item.get("runtime"), RuntimeBucket, RuntimeBucket.UNKNOWN)
    relevance = item.get("relevance", 0.0)
    candidate_id = item.get("id", item.get("candidate_id"))
    return TestCandidate(
        kind,
        str(item.get("command", "")),
        None if candidate_id is None else str(candidate_id),
        _safe_relevance(relevance),
        coverage,
        runtime,
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
    *,
    signals: Sequence[ChangeSignal | str] = (),
) -> TestOrderResult:
    """Rank with allowlisted metadata; retain required tests, and infer nothing from unknowns."""
    normalized_surface = _enum_value(surface, ChangedSurface, ChangedSurface.UNKNOWN)
    signal_items = () if isinstance(signals, (str, bytes)) or not isinstance(signals, Sequence) else signals
    normalized_signals = tuple(sorted({
        signal.value for item in signal_items
        if (signal := _enum_value(item, ChangeSignal, None)) is not None
    }))
    normalized_candidates = tuple(_candidate(item) for item in candidates)
    local_candidates = tuple(
        candidate if candidate.candidate_id is not None else TestCandidate(
            candidate.kind, candidate.command, f"t{index}", candidate.relevance,
            candidate.coverage, candidate.runtime,
        )
        for index, candidate in enumerate(normalized_candidates, start=1)
    )
    mandatory = tuple(_required(item) for item in required)
    fallback = _local_order(local_candidates)
    if len(local_candidates) < 2:
        return TestOrderResult(fallback, mandatory, NO_REMOTE_CHOICE)

    # Keep the established Python unit-before-contract rule only for legacy
    # inputs without informative coverage/runtime metadata.
    if (normalized_surface is ChangedSurface.PYTHON and len(local_candidates) == 2
            and not normalized_signals
            and {item.kind for item in local_candidates} == {TestKind.UNIT, TestKind.CONTRACT}
            and all(item.coverage is Coverage.UNKNOWN and item.runtime is RuntimeBucket.UNKNOWN
                    for item in local_candidates)):
        unit = next(item for item in local_candidates if item.kind is TestKind.UNIT)
        contract = next(item for item in local_candidates if item.kind is TestKind.CONTRACT)
        if unit.relevance >= contract.relevance:
            return TestOrderResult((unit, contract), mandatory, NO_REMOTE_CHOICE)

    # Skip the remote choice only for the specifically evidenced direct+fast
    # candidate against alternatives that are all indirect+slow.
    dominant = [
        candidate for candidate in local_candidates
        if candidate.coverage is Coverage.DIRECT and candidate.runtime is RuntimeBucket.FAST
        and all(
            candidate is other or
            (other.coverage is Coverage.INDIRECT and other.runtime is RuntimeBucket.SLOW)
            for other in local_candidates
        )
    ]
    if len(dominant) == 1:
        winner = dominant[0]
        return TestOrderResult((winner,) + tuple(item for item in fallback if item is not winner),
                               mandatory, NO_REMOTE_CHOICE)

    signatures = {
        (candidate.kind, candidate.coverage, candidate.runtime)
        for candidate in local_candidates
    }
    scores = sorted((candidate.relevance for candidate in local_candidates), reverse=True)
    if len(signatures) < 2 or scores[0] - scores[1] > 0.20:
        return TestOrderResult(fallback, mandatory, NO_REMOTE_CHOICE)

    opaque_ids = {f"t{index}": candidate for index, candidate in enumerate(local_candidates, start=1)}
    criteria = {
        opaque_id: KIND_DESCRIPTORS[candidate.kind]
        for opaque_id, candidate in opaque_ids.items()
    }
    state = {
        "changed_surface": normalized_surface.value,
        "signals": list(normalized_signals),
        "candidates": [
            {
                "id": opaque_id,
                "kind": candidate.kind.value,
                "descriptor": KIND_DESCRIPTORS[candidate.kind],
                "coverage": candidate.coverage.value,
                "runtime": candidate.runtime.value,
            }
            for opaque_id, candidate in opaque_ids.items()
        ],
    }
    questions = {
        "first": {
            "type": "choice",
            "instructions": (
                "Choose the first test candidate by balancing allowlisted change signals, "
                "coverage, and runtime for early useful feedback. Required checks remain unchanged."
            ),
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
        if (not math.isfinite(confidence_value) or confidence_value < CONFIDENCE_THRESHOLD
                or confidence_value > 1.0):
            return TestOrderResult(fallback, mandatory, NO_REMOTE_CHOICE)
        selected = opaque_ids[selected_id]
        ordered = (selected,) + tuple(candidate for candidate in fallback if candidate is not selected)
        return TestOrderResult(ordered, mandatory, REMOTE_CHOICE)
    except Exception:
        return TestOrderResult(fallback, mandatory, NO_REMOTE_CHOICE)
