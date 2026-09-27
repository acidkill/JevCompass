"""Privacy-preserving pretask coding strategy selection.

Only allowlisted task kinds, signals, strategy IDs, and catalog text can reach the
optional Decisions API. Free-form prompts, code, and paths are never accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Iterable, Literal

from .decisions import DecisionUsage, DecisionsClient, DecisionsError

_USAGE_CLIENT_TYPE = DecisionsClient


class TaskKind(str, Enum):
    CODING = "coding"
    DEBUGGING = "debugging"
    TESTING = "testing"
    REVIEW = "review"


class TaskSignal(str, Enum):
    FAILING_TEST = "failing_test"
    DEPENDENCY_CHANGE = "dependency_change"
    EXISTING_SYMBOL = "existing_symbol"
    BEHAVIOR_CHANGE = "behavior_change"
    UNCLEAR_CONTRACT = "unclear_contract"
    REGRESSION_RISK = "regression_risk"
    DATA_FLOW = "data_flow"


class ContractEvidence(str, Enum):
    """Allowlisted local evidence about a requested behavior contract."""

    CONSISTENT = "consistent"
    CONFLICTING = "conflicting"
    ABSENT = "absent"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class StrategyId(str, Enum):
    REPRODUCE_FAILURE = "reproduce_failure"
    INSPECT_DEPENDENCY_OR_SYMBOL_USE = "inspect_dependency_or_symbol_use"
    DEFINE_CONTRACT_THEN_IMPLEMENT = "define_contract_then_implement"
    TRACE_DATA_FLOW = "trace_data_flow"


@dataclass(frozen=True)
class _Strategy:
    id: StrategyId
    task_kinds: frozenset[TaskKind]
    signals: frozenset[TaskSignal]
    avoid_signals: frozenset[TaskSignal]
    priority: int
    rationale: str


@dataclass(frozen=True)
class StrategyRecommendation:
    """A strategy selected from local reviewed content, never backend prose."""

    id: StrategyId
    rationale: str


@dataclass(frozen=True)
class StrategyResult:
    """Recommendations and whether an accepted remote choice reordered them."""

    recommendations: tuple[StrategyRecommendation, ...]
    status: Literal["remote-choice", "no-remote-choice"]
    usage: DecisionUsage | None = None


_CATALOG = (
    _Strategy(
        StrategyId.REPRODUCE_FAILURE,
        frozenset({TaskKind.DEBUGGING, TaskKind.TESTING}),
        frozenset({TaskSignal.FAILING_TEST}),
        frozenset({TaskSignal.BEHAVIOR_CHANGE}),
        0,
        "Reproduce the failure and isolate its cause before changing code.",
    ),
    _Strategy(
        StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE,
        frozenset({TaskKind.CODING, TaskKind.DEBUGGING, TaskKind.REVIEW}),
        frozenset({TaskSignal.DEPENDENCY_CHANGE, TaskSignal.EXISTING_SYMBOL}),
        frozenset({TaskSignal.UNCLEAR_CONTRACT}),
        1,
        "Inspect dependency behavior and existing symbol use before editing.",
    ),
    _Strategy(
        StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT,
        frozenset({TaskKind.CODING, TaskKind.TESTING}),
        frozenset({TaskSignal.BEHAVIOR_CHANGE, TaskSignal.UNCLEAR_CONTRACT}),
        frozenset({TaskSignal.FAILING_TEST}),
        2,
        "Define the expected behavior and contract, then implement against it.",
    ),
    _Strategy(
        StrategyId.TRACE_DATA_FLOW,
        frozenset({TaskKind.DEBUGGING, TaskKind.REVIEW}),
        frozenset({TaskSignal.DATA_FLOW, TaskSignal.REGRESSION_RISK}),
        frozenset({TaskSignal.UNCLEAR_CONTRACT}),
        3,
        "Trace the affected data flow and identify the narrowest safe change.",
    ),
)

_CONFIDENCE_THRESHOLD = 0.65
_SIMILAR_EVIDENCE_GAP = 1
_DEPENDENCY_MIGRATION_GUIDANCE = (
    "Dependency migration checklist: verify the new API and signature; preserve "
    "the callers' public contract; validate exception propagation and resource ownership."
)


def _enum_value(enum_type: type[Enum], value: object) -> Enum | None:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            return None
    return None


def _normalize_inputs(
    task_kind: TaskKind | str, signals: Iterable[TaskSignal | str],
) -> tuple[TaskKind, frozenset[TaskSignal]] | None:
    kind = _enum_value(TaskKind, task_kind)
    if kind is None or isinstance(signals, (str, bytes)):
        return None
    try:
        normalized = [_enum_value(TaskSignal, signal) for signal in signals]
    except (TypeError, ValueError):
        return None
    if any(signal is None for signal in normalized):
        return None
    return kind, frozenset(normalized)  # type: ignore[arg-type]


def _rank(kind: TaskKind, signals: frozenset[TaskSignal]) -> list[tuple[_Strategy, int]]:
    ranked: list[tuple[_Strategy, int]] = []
    for strategy in _CATALOG:
        if kind not in strategy.task_kinds or signals & strategy.avoid_signals:
            continue
        matched = signals & strategy.signals
        if not matched:
            continue
        ranked.append((strategy, len(matched)))
    return sorted(ranked, key=lambda item: (-item[1], item[0].priority))


def _remote_choice(
    ranked: list[tuple[_Strategy, int]],
    kind: TaskKind,
    signals: frozenset[TaskSignal],
    client: DecisionsClient,
    contract_evidence: ContractEvidence | None = None,
) -> StrategyId | None:
    if len(ranked) < 2 or ranked[0][1] - ranked[1][1] > _SIMILAR_EVIDENCE_GAP:
        return None

    options = {
        strategy.id.value: strategy.rationale
        for strategy, _score in ranked
    }
    state = {
        "task_kind": kind.value,
        "signals": sorted(signal.value for signal in signals),
    }
    if contract_evidence is ContractEvidence.PARTIAL:
        state["contract_evidence"] = contract_evidence.value
    questions = {
        "strategy": {
            "type": "choice",
            "instructions": "Which applicable strategy should the coding agent try first for this coarse task signal?",
            "criteria": options,
        }
    }
    try:
        answers = client.decide(state, questions)
    except (DecisionsError, TimeoutError, OSError, TypeError, ValueError):
        return None

    if not isinstance(answers, dict) or set(answers) != {"strategy"}:
        return None
    answer = answers.get("strategy")
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        return None
    selected = answer.get("choice")
    confidence = answer.get("confidence")
    valid_ids = {strategy.id for strategy, _score in ranked}
    if not isinstance(selected, str):
        return None
    try:
        strategy_id = StrategyId(selected)
    except ValueError:
        return None
    if strategy_id not in valid_ids:
        return None
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    try:
        confidence_is_valid = (
            math.isfinite(confidence)
            and _CONFIDENCE_THRESHOLD <= confidence <= 1
        )
    except OverflowError:
        return None
    if not confidence_is_valid:
        return None
    return strategy_id


class _MeteredAnswers:
    """Preserve validated provider usage even when choice validation abstains."""

    def __init__(self, client):
        self.client = client
        self.usage = None

    def decide(self, state, questions):
        response = self.client.decide_with_usage(state, questions)
        self.usage = response.usage
        return response.answers


def _recommendation(
    strategy: _Strategy, signals: frozenset[TaskSignal], *,
    resolve_contract_evidence: bool = False,
) -> StrategyRecommendation:
    rationale = strategy.rationale
    if (
        strategy.id is StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE
        and TaskSignal.DEPENDENCY_CHANGE in signals
    ):
        rationale = f"{rationale} {_DEPENDENCY_MIGRATION_GUIDANCE}"
    if resolve_contract_evidence:
        rationale = (
            f"{rationale} Before editing, establish the required behavior and "
            "precedence from available evidence, resolving any conflict explicitly. "
            "If a failure is provided, preserve its original test and symptom; add "
            "a regression that distinguishes the plausible rules, and retain all "
            "mandatory validation commands."
        )
    return StrategyRecommendation(id=strategy.id, rationale=rationale)


def choose_strategies(
    task_kind: TaskKind | str,
    signals: Iterable[TaskSignal | str],
    *,
    client: DecisionsClient | None = None,
    resolved_strategy: StrategyId | str | None = None,
    contract_evidence: ContractEvidence | str | None = None,
) -> StrategyResult:
    """Return reviewed strategies from allowlisted metadata.

    Explicit resolutions and contract evidence must come from verified local
    observations, never prompt inference. Contract evidence changes strategy
    selection only for coding tasks with exactly existing_symbol and
    behavior_change signals. A local resolution never constructs a client or
    sends a request. Partial evidence is only forwarded as its enum ID when the
    existing ambiguous remote choice path runs; unknown or omitted evidence
    leaves legacy behavior unchanged.
    """
    evidence_id = None
    if contract_evidence is not None:
        try:
            evidence_id = ContractEvidence(contract_evidence)
        except (TypeError, ValueError):
            return StrategyResult((), "no-remote-choice")

    resolved_id = None
    if resolved_strategy is not None:
        try:
            resolved_id = StrategyId(resolved_strategy)
        except (TypeError, ValueError):
            return StrategyResult((), "no-remote-choice")

    normalized = _normalize_inputs(task_kind, signals)
    if normalized is None:
        return StrategyResult((), "no-remote-choice")
    kind, signal_set = normalized
    ranked = _rank(kind, signal_set)
    if not ranked:
        return StrategyResult((), "no-remote-choice")

    contract_scope = (
        kind is TaskKind.CODING
        and signal_set == frozenset({TaskSignal.EXISTING_SYMBOL, TaskSignal.BEHAVIOR_CHANGE})
    )
    contract_resolution = None
    if contract_scope and evidence_id in {ContractEvidence.CONSISTENT}:
        contract_resolution = StrategyId.INSPECT_DEPENDENCY_OR_SYMBOL_USE
    elif contract_scope and evidence_id in {ContractEvidence.CONFLICTING, ContractEvidence.ABSENT}:
        contract_resolution = StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT

    if contract_resolution is not None:
        if resolved_id is not None and resolved_id is not contract_resolution:
            return StrategyResult((), "no-remote-choice")
        resolved_id = contract_resolution

    if resolved_id is not None:
        for strategy, _score in ranked:
            if strategy.id == resolved_id:
                return StrategyResult(
                    (_recommendation(
                        strategy, signal_set,
                        resolve_contract_evidence=(
                            contract_resolution is StrategyId.DEFINE_CONTRACT_THEN_IMPLEMENT
                            and evidence_id in {
                                ContractEvidence.CONFLICTING, ContractEvidence.ABSENT,
                            }
                        ),
                    ),),
                    "no-remote-choice",
                )
        return StrategyResult((), "no-remote-choice")

    usage = None
    status: Literal["remote-choice", "no-remote-choice"] = "no-remote-choice"
    if len(ranked) >= 2 and ranked[0][1] - ranked[1][1] <= _SIMILAR_EVIDENCE_GAP:
        decision_client = client if client is not None else DecisionsClient()
        metered = _MeteredAnswers(decision_client) if isinstance(decision_client, _USAGE_CLIENT_TYPE) else None
        selected = _remote_choice(
            ranked, kind, signal_set, metered or decision_client,
            evidence_id if contract_scope and evidence_id is ContractEvidence.PARTIAL else None,
        )
        usage = metered.usage if metered is not None else None
        if selected is not None:
            ranked.sort(key=lambda item: (item[0].id != selected, -item[1], item[0].priority))
            status = "remote-choice"

    recommendations = tuple(
        _recommendation(strategy, signal_set)
        for strategy, _score in ranked[:2]
    )
    return StrategyResult(recommendations, status, usage)
