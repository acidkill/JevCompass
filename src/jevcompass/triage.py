"""Privacy-preserving triage for ambiguous test failures.

Callers provide only locally classified enums and the observed process exit
status. This module never accepts or executes commands, and the status reported
by the test runner is preserved verbatim in every result.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Mapping, Sequence

from .decisions import DecisionsClient, DecisionResponse, DecisionUsage
from ._typed_decision_cache import TypedDecisionCache


NO_REMOTE_CHOICE = "no-remote-choice"
REMOTE_CHOICE = "remote-choice"
DECISION_TIMEOUT = 1.0
CONFIDENCE_THRESHOLD = 0.70
_USAGE_CLIENT_TYPE = DecisionsClient


class FailureKind(str, Enum):
    """Allowlisted, locally classified test-failure categories."""

    ASSERTION = "assertion"
    COLLECTION = "collection"
    DEPENDENCY = "dependency"
    ENVIRONMENT = "environment"
    IMPORT = "import"
    NETWORK = "network"
    PERMISSION = "permission"
    TIMEOUT = "timeout"


class HypothesisId(str, Enum):
    """Reviewed hypothesis identifiers; arbitrary caller text is never accepted."""

    ASSERTION_EXPECTATION_DRIFT = "assertion_expectation_drift"
    ASSERTION_BEHAVIOR_REGRESSION = "assertion_behavior_regression"
    CONFIRM_BEHAVIOR_CONTRACT = "confirm_behavior_contract"
    COLLECTION_SYNTAX = "collection_syntax"
    COLLECTION_IMPORT_SIDE_EFFECT = "collection_import_side_effect"
    DEPENDENCY_MISSING = "dependency_missing"
    DEPENDENCY_VERSION_CONFLICT = "dependency_version_conflict"
    ENVIRONMENT_CONFIG_MISSING = "environment_config_missing"
    ENVIRONMENT_PLATFORM_MISMATCH = "environment_platform_mismatch"
    IMPORT_MODULE_MISSING = "import_module_missing"
    IMPORT_PATH_CHANGED = "import_path_changed"
    NETWORK_SERVICE_UNAVAILABLE = "network_service_unavailable"
    NETWORK_CONNECTIVITY = "network_connectivity"
    PERMISSION_ACCESS_DENIED = "permission_access_denied"
    PERMISSION_SANDBOX_RESTRICTION = "permission_sandbox_restriction"
    TIMEOUT_CONTENTION = "timeout_contention"
    TIMEOUT_NONTERMINATING = "timeout_nonterminating"


class AssertionObservation(str, Enum):
    """Allowlisted assertion-contract observations from local review."""

    CONTRACT_UNDERSPECIFIED = "contract_underspecified"
    CONTRACT_CONFIRMED = "contract_confirmed"
    LEGACY_FIXTURE_CONFLICT = "legacy_fixture_conflict"


class ImportObservation(str, Enum):
    """Allowlisted facts from a local, read-only import-spec check."""

    PACKAGE_PRESENT = "package_present"
    PACKAGE_ABSENT = "package_absent"
    TARGET_MODULE_PRESENT = "target_module_present"
    TARGET_MODULE_ABSENT = "target_module_absent"
    REPLACEMENT_MODULE_PRESENT = "replacement_module_present"
    REPLACEMENT_MODULE_ABSENT = "replacement_module_absent"


class TimeoutObservation(str, Enum):
    """Allowlisted timeout facts established by verified local review."""

    PROGRESS_OBSERVED = "progress_observed"
    NO_PROGRESS_OBSERVED = "no_progress_observed"
    RESOURCE_CONTENTION_OBSERVED = "resource_contention_observed"
    NO_RESOURCE_CONTENTION_OBSERVED = "no_resource_contention_observed"
    WAIT_CONDITION_SATISFIABLE = "wait_condition_satisfiable"
    WAIT_CONDITION_UNSATISFIABLE = "wait_condition_unsatisfiable"


@dataclass(frozen=True)
class DiagnosticStep:
    """A locally authored next diagnostic action."""

    id: HypothesisId
    title: str
    instruction: str


class TriageDecisionReason(str, Enum):
    """Fixed local outcome labels; never contains provider or exception text."""

    LOCAL_RESOLUTION = "local_resolution"
    LOCAL_ABSTENTION = "local_abstention"
    INVALID_RESPONSE = "invalid_response"
    INSUFFICIENT_CONFIDENCE = "insufficient_confidence"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    ACCEPTED = "accepted"


@dataclass(frozen=True)
class TriageResult:
    """A bounded recommendation that retains the observed test outcome."""

    observed_exit_status: int
    steps: tuple[DiagnosticStep, ...]
    status: str
    decision_usage: DecisionUsage | None = None
    decision_reason: TriageDecisionReason | None = None
    cache_hit: bool = False
    hypothesis_order: tuple[HypothesisId, ...] = ()
    hypothesis_ranking_status: str = "not_established"

    @property
    def test_failed(self) -> bool:
        """Whether the original test process reported failure."""
        return self.observed_exit_status != 0


@dataclass(frozen=True)
class _CatalogEntry:
    kind: FailureKind
    descriptor: str
    step: DiagnosticStep


def _entry(kind: FailureKind, hypothesis: HypothesisId, descriptor: str,
           title: str, instruction: str) -> _CatalogEntry:
    return _CatalogEntry(kind, descriptor, DiagnosticStep(hypothesis, title, instruction))


CATALOG: Mapping[HypothesisId, _CatalogEntry] = {
    item.step.id: item for item in (
        _entry(FailureKind.ASSERTION, HypothesisId.ASSERTION_EXPECTATION_DRIFT,
               "expected behavior or fixture data changed",
               "Check the test expectation and fixture",
               "Compare the failing assertion's expected value with the current fixture contract."),
        _entry(FailureKind.ASSERTION, HypothesisId.ASSERTION_BEHAVIOR_REGRESSION,
               "application behavior changed unexpectedly",
               "Trace the behavior change",
               "Compare the affected behavior with the last known passing test contract."),
        _entry(FailureKind.ASSERTION, HypothesisId.CONFIRM_BEHAVIOR_CONTRACT,
               "the authoritative behavior contract is unclear",
               "Confirm the behavior contract",
               "Establish the authoritative behavior semantics before repairing the assertion; defer any edit unsupported by that contract."),
        _entry(FailureKind.COLLECTION, HypothesisId.COLLECTION_SYNTAX,
               "test source may not parse",
               "Inspect test syntax",
               "Check the reported test module for a syntax or declaration error."),
        _entry(FailureKind.COLLECTION, HypothesisId.COLLECTION_IMPORT_SIDE_EFFECT,
               "test discovery may trigger an import failure",
               "Check imports during discovery",
               "Inspect imports executed while the test module is collected."),
        _entry(FailureKind.DEPENDENCY, HypothesisId.DEPENDENCY_MISSING,
               "a required package may be absent",
               "Check declared dependencies",
               "Compare the project's declared test dependencies with the active environment."),
        _entry(FailureKind.DEPENDENCY, HypothesisId.DEPENDENCY_VERSION_CONFLICT,
               "installed package versions may be incompatible",
               "Check dependency constraints",
               "Compare installed package versions with the project's supported constraints."),
        _entry(FailureKind.ENVIRONMENT, HypothesisId.ENVIRONMENT_CONFIG_MISSING,
               "required test configuration may be absent",
               "Check test configuration",
               "Verify required test settings are defined in the intended test environment."),
        _entry(FailureKind.ENVIRONMENT, HypothesisId.ENVIRONMENT_PLATFORM_MISMATCH,
               "the active platform may differ from the supported target",
               "Check platform assumptions",
               "Compare the test's platform assumptions with the current supported environment."),
        _entry(FailureKind.IMPORT, HypothesisId.IMPORT_MODULE_MISSING,
               "an imported module may not be installed",
               "Check module availability",
               "Verify the imported module is provided by the declared runtime or test dependencies."),
        _entry(FailureKind.IMPORT, HypothesisId.IMPORT_PATH_CHANGED,
               "the package import path may have changed",
               "Check package import structure",
               "Compare the failing import with the package's current module layout."),
        _entry(FailureKind.NETWORK, HypothesisId.NETWORK_SERVICE_UNAVAILABLE,
               "a required test service may be unavailable",
               "Check test service readiness",
               "Verify the required test service is available in the configured test environment."),
        _entry(FailureKind.NETWORK, HypothesisId.NETWORK_CONNECTIVITY,
               "the test environment may not reach a required service",
               "Check test network access",
               "Verify the configured test environment can reach its required service."),
        _entry(FailureKind.PERMISSION, HypothesisId.PERMISSION_ACCESS_DENIED,
               "the test process may lack resource access",
               "Check resource permissions",
               "Verify the test process is allowed to access the required test resource."),
        _entry(FailureKind.PERMISSION, HypothesisId.PERMISSION_SANDBOX_RESTRICTION,
               "a sandbox policy may block the test operation",
               "Check sandbox policy",
               "Compare the failing operation with the active test sandbox restrictions."),
        _entry(FailureKind.TIMEOUT, HypothesisId.TIMEOUT_CONTENTION,
               "resource contention may have delayed test progress",
               "Check test resource contention",
               "Review whether concurrent test activity could delay this test's required resource."),
        _entry(FailureKind.TIMEOUT, HypothesisId.TIMEOUT_NONTERMINATING,
               "the test may be waiting on a condition that never completes",
               "Inspect the wait condition",
               "Check whether the wait condition can be satisfied on every path under the written contract. "
               "If local observations mark it unsatisfiable, inspect that path; treat the timeout as a lead, "
               "not a confirmed diagnosis. If the contract supports a minimal source fix, "
               "make it and rerun the same focused check that failed. A passing rerun confirms only that "
               "check; complete every mandatory validation afterward."),
    )
}


def _empty_result(exit_status: int) -> TriageResult:
    return TriageResult(
        exit_status, (), NO_REMOTE_CHOICE,
        decision_reason=TriageDecisionReason.LOCAL_RESOLUTION,
    )


def _confidence_reason(value: Any) -> TriageDecisionReason | None:
    """Classify confidence without retaining malformed values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return TriageDecisionReason.INVALID_RESPONSE
    try:
        confidence = float(value)
    except (OverflowError, ValueError):
        return TriageDecisionReason.INVALID_RESPONSE
    if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
        return TriageDecisionReason.INVALID_RESPONSE
    if confidence < CONFIDENCE_THRESHOLD:
        return TriageDecisionReason.INSUFFICIENT_CONFIDENCE
    return None


def _pairwise_rank_questions(
    plausible: Sequence[_CatalogEntry],
) -> tuple[dict[str, dict[str, Any]], tuple[tuple[str, HypothesisId, HypothesisId], ...]]:
    """Build a bounded pairwise choice tournament from fixed hypothesis enums."""
    questions: dict[str, dict[str, Any]] = {}
    pairs: list[tuple[str, HypothesisId, HypothesisId]] = []
    for left_index, left in enumerate(plausible):
        for right_index in range(left_index + 1, len(plausible)):
            right = plausible[right_index]
            question_id = f"hypothesis_pair_{left_index}_{right_index}"
            left_id, right_id = left.step.id, right.step.id
            questions[question_id] = {
                "type": "choice",
                "instructions": "Which candidate is the more plausible explanation, based only on the supplied allowlisted evidence?",
                "criteria": {
                    left_id.value: left.descriptor,
                    right_id.value: right.descriptor,
                },
            }
            pairs.append((question_id, left_id, right_id))
    return questions, tuple(pairs)


def _validated_pairwise_order(
    answers: Mapping[str, Any],
    expected_question_ids: set[str],
    pairs: Sequence[tuple[str, HypothesisId, HypothesisId]],
) -> tuple[HypothesisId, ...] | None:
    """Return a full order only for a complete, confident, acyclic tournament."""
    if set(answers) != expected_question_ids:
        return None
    nodes: set[HypothesisId] = set()
    outgoing: dict[HypothesisId, set[HypothesisId]] = {}
    indegree: dict[HypothesisId, int] = {}
    for question_id, left, right in pairs:
        nodes.update((left, right))
        answer = answers.get(question_id)
        if not isinstance(answer, Mapping) or answer.get("type") != "choice":
            return None
        winner_token = answer.get("choice")
        if not isinstance(winner_token, str) or winner_token not in {left.value, right.value}:
            return None
        if _confidence_reason(answer.get("confidence")) is not None:
            return None
        winner = left if winner_token == left.value else right
        loser = right if winner is left else left
        outgoing.setdefault(winner, set()).add(loser)
        indegree.setdefault(winner, 0)
        indegree[loser] = indegree.get(loser, 0) + 1
    order: list[HypothesisId] = []
    remaining = set(nodes)
    while remaining:
        sources = [node for node in remaining if indegree.get(node, 0) == 0]
        # An acyclic complete tournament has exactly one source at each step.
        if len(sources) != 1:
            return None
        winner = sources[0]
        order.append(winner)
        remaining.remove(winner)
        for loser in outgoing.get(winner, ()):
            indegree[loser] -= 1
    return tuple(order) if len(order) == len(nodes) else None


def _normalize_import_observations(
    observations: Sequence[ImportObservation] | None,
) -> tuple[ImportObservation, ...]:
    if observations is None:
        return ()
    if (isinstance(observations, (str, bytes))
            or not isinstance(observations, Sequence)
            or any(not isinstance(item, ImportObservation) for item in observations)):
        raise TypeError("import-observations-must-be-enums")
    return tuple(dict.fromkeys(observations))


def _normalize_assertion_observations(
    observations: Sequence[AssertionObservation] | None,
) -> tuple[AssertionObservation, ...]:
    if observations is None:
        return ()
    if (isinstance(observations, (str, bytes))
            or not isinstance(observations, Sequence)
            or any(not isinstance(item, AssertionObservation) for item in observations)):
        raise TypeError("assertion-observations-must-be-enums")
    return tuple(dict.fromkeys(observations))


def _normalize_timeout_observations(
    observations: Sequence[TimeoutObservation] | None,
) -> tuple[TimeoutObservation, ...]:
    if observations is None:
        return ()
    if (isinstance(observations, (str, bytes))
            or not isinstance(observations, Sequence)
            or any(not isinstance(item, TimeoutObservation) for item in observations)):
        raise TypeError("timeout-observations-must-be-enums")
    return tuple(dict.fromkeys(observations))


def _contradictory_timeout_observations(
    observations: Sequence[TimeoutObservation],
) -> bool:
    known = set(observations)
    contradictions = (
        (TimeoutObservation.PROGRESS_OBSERVED, TimeoutObservation.NO_PROGRESS_OBSERVED),
        (TimeoutObservation.RESOURCE_CONTENTION_OBSERVED,
         TimeoutObservation.NO_RESOURCE_CONTENTION_OBSERVED),
        (TimeoutObservation.WAIT_CONDITION_SATISFIABLE,
         TimeoutObservation.WAIT_CONDITION_UNSATISFIABLE),
    )
    return any(left in known and right in known for left, right in contradictions)


def _contradictory_import_observations(
    observations: Sequence[ImportObservation],
) -> bool:
    known = set(observations)
    contradictions = (
        (ImportObservation.PACKAGE_PRESENT, ImportObservation.PACKAGE_ABSENT),
        (ImportObservation.TARGET_MODULE_PRESENT, ImportObservation.TARGET_MODULE_ABSENT),
        (ImportObservation.REPLACEMENT_MODULE_PRESENT, ImportObservation.REPLACEMENT_MODULE_ABSENT),
    )
    if any(left in known and right in known for left, right in contradictions):
        return True
    return (
        ImportObservation.PACKAGE_ABSENT in known
        and bool(known.intersection({
            ImportObservation.TARGET_MODULE_PRESENT,
            ImportObservation.REPLACEMENT_MODULE_PRESENT,
        }))
    )


def _import_path_is_locally_confirmed(
    observations: Sequence[ImportObservation],
) -> bool:
    return {
        ImportObservation.PACKAGE_PRESENT,
        ImportObservation.TARGET_MODULE_ABSENT,
        ImportObservation.REPLACEMENT_MODULE_PRESENT,
    }.issubset(observations)


def triage_failure(
    failure_kinds: Sequence[FailureKind],
    hypotheses: Sequence[HypothesisId],
    observed_exit_status: int,
    client: Any | None = None,
    *,
    import_observations: Sequence[ImportObservation] | None = None,
    assertion_observations: Sequence[AssertionObservation] | None = None,
    timeout_observations: Sequence[TimeoutObservation] | None = None,
    rank_hypotheses: bool = False,
) -> TriageResult:
    """Return up to two diagnostic steps from allowlisted failure metadata.

    Import observations are fixed enum tokens from a local, read-only check.
    A consistent package-present / target-absent / replacement-present result
    rules out the missing-package hypothesis locally and skips Jev. For assertion
    failures, an underspecified contract permits only a supplied local contract
    confirmation step; contradictory contract observations abstain. A legacy
    fixture conflict may be ranked remotely using only its enum token. Raw names,
    paths, output, prompts, commands, and free-form descriptions are never accepted.
    The optional decision reason is a fixed safe enum and retains no exception text.
    """
    if isinstance(observed_exit_status, bool) or not isinstance(observed_exit_status, int):
        raise TypeError("observed-exit-status-must-be-int")
    if (isinstance(failure_kinds, (str, bytes))
            or not isinstance(failure_kinds, Sequence)
            or any(not isinstance(kind, FailureKind) for kind in failure_kinds)):
        raise TypeError("failure-kinds-must-be-enums")
    if (isinstance(hypotheses, (str, bytes))
            or not isinstance(hypotheses, Sequence)
            or any(not isinstance(hypothesis, HypothesisId) for hypothesis in hypotheses)):
        raise TypeError("hypotheses-must-be-enums")
    if not isinstance(rank_hypotheses, bool):
        raise TypeError("rank-hypotheses-must-be-bool")
    observations = _normalize_import_observations(import_observations)
    assertion_facts = _normalize_assertion_observations(assertion_observations)
    timeout_facts = _normalize_timeout_observations(timeout_observations)
    if observed_exit_status == 0:
        return _empty_result(observed_exit_status)

    allowed_kinds = set(failure_kinds)
    timeout_fact_set = set(timeout_facts)
    timeout_evidence_applies = FailureKind.TIMEOUT in allowed_kinds and bool(timeout_facts)
    import_evidence_applies = FailureKind.IMPORT in allowed_kinds and bool(observations)
    if timeout_evidence_applies and _contradictory_timeout_observations(timeout_facts):
        return TriageResult(observed_exit_status, (), NO_REMOTE_CHOICE,
                            decision_reason=TriageDecisionReason.LOCAL_ABSTENTION)
    if import_evidence_applies and _contradictory_import_observations(observations):
        return TriageResult(observed_exit_status, (), NO_REMOTE_CHOICE,
                            decision_reason=TriageDecisionReason.LOCAL_ABSTENTION)

    assertion_evidence_applies = (
        FailureKind.ASSERTION in allowed_kinds and bool(assertion_facts)
    )
    assertion_fact_set = set(assertion_facts)
    if (assertion_evidence_applies
            and AssertionObservation.CONTRACT_UNDERSPECIFIED in assertion_fact_set
            and AssertionObservation.CONTRACT_CONFIRMED in assertion_fact_set):
        return TriageResult(observed_exit_status, (), NO_REMOTE_CHOICE,
                            decision_reason=TriageDecisionReason.LOCAL_ABSTENTION)

    locally_confirmed_path = (
        import_evidence_applies and _import_path_is_locally_confirmed(observations)
    )
    if (assertion_evidence_applies
            and AssertionObservation.CONTRACT_UNDERSPECIFIED in assertion_fact_set):
        if HypothesisId.CONFIRM_BEHAVIOR_CONTRACT not in hypotheses:
            return TriageResult(observed_exit_status, (), NO_REMOTE_CHOICE,
                                decision_reason=TriageDecisionReason.LOCAL_ABSTENTION)
        contract_entry = CATALOG[HypothesisId.CONFIRM_BEHAVIOR_CONTRACT]
        return TriageResult(observed_exit_status, (contract_entry.step,), NO_REMOTE_CHOICE,
                            decision_reason=TriageDecisionReason.LOCAL_RESOLUTION)

    plausible: list[_CatalogEntry] = []
    seen: set[HypothesisId] = set()
    for hypothesis in hypotheses:
        entry = CATALOG[hypothesis]
        if entry.kind not in allowed_kinds or hypothesis in seen:
            continue
        seen.add(hypothesis)
        if locally_confirmed_path and hypothesis is HypothesisId.IMPORT_MODULE_MISSING:
            continue
        if (assertion_evidence_applies
                and AssertionObservation.CONTRACT_CONFIRMED in assertion_fact_set
                and hypothesis is HypothesisId.CONFIRM_BEHAVIOR_CONTRACT):
            continue
        plausible.append(entry)

    eligible_ids = {entry.step.id for entry in plausible}
    if timeout_evidence_applies:
        if (TimeoutObservation.WAIT_CONDITION_UNSATISFIABLE in timeout_fact_set
                and HypothesisId.TIMEOUT_NONTERMINATING in eligible_ids):
            return TriageResult(
                observed_exit_status,
                (CATALOG[HypothesisId.TIMEOUT_NONTERMINATING].step,),
                NO_REMOTE_CHOICE,
                decision_reason=TriageDecisionReason.LOCAL_RESOLUTION,
            )
        contention_progress_wait = {
            TimeoutObservation.RESOURCE_CONTENTION_OBSERVED,
            TimeoutObservation.PROGRESS_OBSERVED,
            TimeoutObservation.WAIT_CONDITION_SATISFIABLE,
        }
        if (contention_progress_wait.issubset(timeout_fact_set)
                and HypothesisId.TIMEOUT_CONTENTION in eligible_ids):
            return TriageResult(
                observed_exit_status,
                (CATALOG[HypothesisId.TIMEOUT_CONTENTION].step,),
                NO_REMOTE_CHOICE,
                decision_reason=TriageDecisionReason.LOCAL_RESOLUTION,
            )

    fallback = tuple(entry.step for entry in plausible[:2])
    if locally_confirmed_path:
        return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE,
                            decision_reason=TriageDecisionReason.LOCAL_RESOLUTION)
    if not failure_kinds or len(plausible) < 2:
        return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE,
                            decision_reason=TriageDecisionReason.LOCAL_ABSTENTION)

    ids = [entry.step.id.value for entry in plausible]
    causal_plausible = [
        entry for entry in plausible
        if entry.step.id is not HypothesisId.CONFIRM_BEHAVIOR_CONTRACT
    ]
    causal_ids = [entry.step.id.value for entry in causal_plausible]
    state = {
        "test_outcome": "failed",
        "failure_kinds": [kind.value for kind in FailureKind if kind in allowed_kinds],
        "hypotheses": causal_ids,
    }
    if import_evidence_applies:
        state["import_observations"] = [item.value for item in observations]
    if assertion_evidence_applies:
        state["assertion_observations"] = [item.value for item in assertion_facts]
    if timeout_evidence_applies:
        state["timeout_observations"] = [item.value for item in timeout_facts]
    questions = {
        "diagnostic": {
            "type": "choice",
            "instructions": "Choose the most useful next diagnostic step.",
            "criteria": {entry.step.id.value: entry.descriptor for entry in plausible},
        }
    }
    rank_questions: dict[str, dict[str, Any]] = {}
    rank_pairs: tuple[tuple[str, HypothesisId, HypothesisId], ...] = ()
    if rank_hypotheses and 2 <= len(causal_plausible) <= 4:
        rank_questions, rank_pairs = _pairwise_rank_questions(causal_plausible)
        questions.update(rank_questions)
    expected_question_ids = set(questions)
    ranking_status = "incomplete" if rank_pairs else "not_established"

    try:
        decision_client = client if client is not None else DecisionsClient(timeout=DECISION_TIMEOUT)
        usage = None
        cache = None
        if client is None and isinstance(decision_client, _USAGE_CLIENT_TYPE) and not rank_hypotheses:
            try:
                cache = TypedDecisionCache(
                    scope="triage", model=decision_client.model,
                    policy_version="triage-choice-v1", confidence_threshold=CONFIDENCE_THRESHOLD,
                    request={"state": state, "questions": questions},
                )
            except Exception:
                cache = None
        try:
            cached = cache.get(ids) if cache is not None else None
        except Exception:
            cached = None
        cache_hit = cached is not None
        if cached is not None:
            answers = {"diagnostic": {"type": "choice", "choice": cached.tokens[0],
                                      "confidence": cached.confidence}}
        elif isinstance(decision_client, _USAGE_CLIENT_TYPE):
            response = decision_client.decide_with_usage(state, questions)
            if not isinstance(response, DecisionResponse):
                return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE,
                                    decision_reason=TriageDecisionReason.INVALID_RESPONSE,
                                    hypothesis_ranking_status=ranking_status)
            answers, usage = response.answers, response.usage
        else:
            # Existing injected clients can implement the historical answers-only API.
            answers = decision_client.decide(state, questions)
        if not isinstance(answers, Mapping):
            return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE, usage,
                                TriageDecisionReason.INVALID_RESPONSE,
                                hypothesis_ranking_status=ranking_status)
        if not rank_pairs and set(answers) != {"diagnostic"}:
            return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE, usage,
                                TriageDecisionReason.INVALID_RESPONSE)
        ranked_order = None
        if rank_pairs:
            ranked_order = _validated_pairwise_order(answers, expected_question_ids, rank_pairs)
            if ranked_order is not None:
                ranking_status = "complete"
        answer = answers.get("diagnostic")
        if not isinstance(answer, Mapping) or answer.get("type") != "choice":
            return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE, usage,
                                TriageDecisionReason.INVALID_RESPONSE,
                                hypothesis_order=ranked_order or (),
                                hypothesis_ranking_status=ranking_status)
        selected_id = answer.get("choice")
        confidence = answer.get("confidence")
        if selected_id not in ids:
            return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE, usage,
                                TriageDecisionReason.INVALID_RESPONSE,
                                hypothesis_order=ranked_order or (),
                                hypothesis_ranking_status=ranking_status)
        reason = _confidence_reason(confidence)
        if reason is not None:
            return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE, usage, reason,
                                hypothesis_order=ranked_order or (),
                                hypothesis_ranking_status=ranking_status)
        if cache is not None and not cache_hit and not rank_hypotheses:
            try:
                cache.put((selected_id,), confidence, eligible_tokens=ids)
            except Exception:
                pass
        selected = CATALOG[HypothesisId(selected_id)].step
        ordered = (selected,) + tuple(step for step in fallback if step.id != selected.id)
        return TriageResult(observed_exit_status, ordered[:2], REMOTE_CHOICE, usage,
                            TriageDecisionReason.ACCEPTED, cache_hit,
                            hypothesis_order=ranked_order or (),
                            hypothesis_ranking_status=ranking_status)
    except TimeoutError:
        return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE,
                            decision_reason=TriageDecisionReason.PROVIDER_TIMEOUT,
                            hypothesis_ranking_status=ranking_status)
    except Exception:
        # DecisionsClient folds multiple transport failures into a safe generic
        # unavailable error; only an unwrapped TimeoutError is called a timeout.
        return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE,
                            decision_reason=TriageDecisionReason.PROVIDER_ERROR,
                            hypothesis_ranking_status=ranking_status)
