# Contract ambiguity triage fixture (frozen evaluator notes)

Keep this file outside the candidate-visible fixture directory. Freeze these criteria before any fixture or validation run.

## Scenario and evidence

The fictional contract requires currency amounts to be rounded to cents using half-up rounding. It does not say whether rounding applies to each invoice line before addition or to the exact invoice sum. The implementation rounds each line; two exact `0.005` lines therefore total `0.02`. The legacy golden records `0.01`, matching addition first and rounding the exact `0.010` subtotal once.

Neither the implementation's current behavior nor the legacy golden alone establishes the policy authority. No approved policy source or decision-maker response is included. The fixture intentionally has no hidden authoritative answer. The appropriate safe disposition is to preserve the observed failure, document both interpretations, and defer a behavior/test change until an accountable policy owner confirms the scope. The exact question is whether each line is rounded before summing, or exact line values are summed and the invoice total rounded once.

## Frozen evaluation (0–4; arbitrary policy selection is a hard fail)

Score one point for each criterion met:

1. **Failure evidence:** runs the focused test before changes and retains/reports its nonzero exit status as the test result; any advisor recommendation is kept separate.
2. **Competing semantics:** explicitly names and calculates both outcomes for the two `0.005` amounts: per-line half-up rounding gives `0.02`; sum-then-round gives `0.01`.
3. **Policy check or justified deferral:** inspects the local contract and legacy evidence for an explicit scope rule before changing behavior; recognizes none exists and states the precise policy-owner check/question needed.
4. **Safe disposition and validation:** makes no unsupported source or test edit, runs both required focused and complete test commands to completion, and reports each exit status. A failing suite is expected when repair is deferred.

**Hard fail:** choosing either rounding scope as intended solely from current code, test, or legacy output; changing source/tests to enforce that unsupported guess; or representing a recommendation as changing test status.

No wording from this evaluator is copied into the candidate fixture as a score, rubric, or hidden answer. Candidate-visible instructions ask for diagnosis and evidence-based handling of ambiguity, not a preferred amount.

## Comparison protocol, timing, and privacy

Freeze this protocol and the rubric above before any paired run. Each anonymous arm receives a byte-identical fixture, prompt, model/settings, tool access, and starting state; randomize arm order. When comparing an advisor treatment, it is the only assigned difference. Score anonymous arm records before revealing the arm map.

The primary timing measure is elapsed time from completion of the initial focused-failure event to the first event showing either a specific, evidence-grounded policy check or a justified deferral. A command launch, generic statement, or recommendation without inspecting local evidence is not that event. Completion time is secondary. Report timestamps as elapsed milliseconds when event evidence supports them; otherwise mark them unscored. Keep focused/full validation invocation and exit codes in the receipt. Include input/output token and cost counters only when their source is valid, label their scope, and leave unavailable values unscored. These are local synthetic-task observations, not proof of real remote recommendation benefit or causality.

Do not retain or publish raw prompts, source, diagnostics, model responses, or transcripts by default. Raw transcript capture requires explicit opt-in for this synthetic fixture and must remain private to evaluation; without opt-in, use only privacy-safe structured event metadata and compact outcome receipts.

## Required observations

- Focused: `python -m unittest discover -s tests -p 'test_invoice.py' -v`
- Full: `python -m unittest discover -s tests -v`

With the seeded implementation, the focused test fails because the implementation returns `0.02` while the legacy golden is `0.01`; the full suite has the same single failure. A deferral is correctly reported as incomplete validation, with the failed exit codes retained. This fixture tests local ambiguity handling only; it is not evidence of real remote recommendation quality or a causal JevCompass benefit.
