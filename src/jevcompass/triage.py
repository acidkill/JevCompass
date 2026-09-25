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

from .decisions import DecisionsClient


NO_REMOTE_CHOICE = "no-remote-choice"
REMOTE_CHOICE = "remote-choice"
DECISION_TIMEOUT = 1.0
CONFIDENCE_THRESHOLD = 0.70


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


@dataclass(frozen=True)
class DiagnosticStep:
    """A locally authored next diagnostic action."""

    id: HypothesisId
    title: str
    instruction: str


@dataclass(frozen=True)
class TriageResult:
    """A bounded recommendation that retains the observed test outcome."""

    observed_exit_status: int
    steps: tuple[DiagnosticStep, ...]
    status: str

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
               "Check whether the test's completion condition can be satisfied on every path."),
    )
}


def _empty_result(exit_status: int) -> TriageResult:
    return TriageResult(exit_status, (), NO_REMOTE_CHOICE)


def _valid_confidence(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    confidence = float(value)
    return math.isfinite(confidence) and CONFIDENCE_THRESHOLD <= confidence <= 1.0


def triage_failure(
    failure_kinds: Sequence[FailureKind],
    hypotheses: Sequence[HypothesisId],
    observed_exit_status: int,
    client: Any | None = None,
) -> TriageResult:
    """Return up to two local diagnostic steps for a failing, ambiguous result.

    Jev is queried only when at least two supplied hypotheses are allowlisted,
    correspond to a supplied failure kind, and produce distinct diagnostics.
    Raw output, prompts, paths, commands and free-form descriptions are not
    accepted by this API or included in a Decisions request.
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
    if observed_exit_status == 0:
        return _empty_result(observed_exit_status)

    allowed_kinds = set(failure_kinds)
    plausible: list[_CatalogEntry] = []
    seen: set[HypothesisId] = set()
    for hypothesis in hypotheses:
        entry = CATALOG[hypothesis]
        if entry.kind in allowed_kinds and hypothesis not in seen:
            plausible.append(entry)
            seen.add(hypothesis)
    fallback = tuple(entry.step for entry in plausible[:2])
    if not failure_kinds or len(plausible) < 2:
        return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE)

    ids = [entry.step.id.value for entry in plausible]
    state = {
        "test_outcome": "failed",
        "failure_kinds": [kind.value for kind in FailureKind if kind in allowed_kinds],
        "hypotheses": ids,
    }
    questions = {
        "diagnostic": {
            "type": "choice",
            "instructions": "Choose the most useful next diagnostic step.",
            "criteria": {entry.step.id.value: entry.descriptor for entry in plausible},
        }
    }

    try:
        decision_client = client if client is not None else DecisionsClient(timeout=DECISION_TIMEOUT)
        answers = decision_client.decide(state, questions)
        if not isinstance(answers, Mapping) or set(answers) != {"diagnostic"}:
            return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE)
        answer = answers.get("diagnostic")
        if not isinstance(answer, Mapping) or answer.get("type") != "choice":
            return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE)
        selected_id = answer.get("choice")
        if selected_id not in ids or not _valid_confidence(answer.get("confidence")):
            return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE)
        selected = CATALOG[HypothesisId(selected_id)].step
        ordered = (selected,) + tuple(step for step in fallback if step.id != selected.id)
        return TriageResult(observed_exit_status, ordered[:2], REMOTE_CHOICE)
    except Exception:
        # Do not retain or surface transport exceptions; they can contain unsafe data.
        return TriageResult(observed_exit_status, fallback, NO_REMOTE_CHOICE)
