# Invoice plan assessment: frozen expected evidence

Status: prospective local-advisor control. No plans generated or scored.

Read with [plan quality protocol](plan_quality_protocol.md). Both arms see the same InvoiceTotals fixture. Treatment uses verified `contract_evidence=conflicting`: the source explicitly sums displayed rounded lines while the legacy golden expects a different total. The half-up contract alone is incomplete about aggregation order, not mathematically self-contradictory. The CLI resolves `define_contract_then_implement` locally with null usage; this case cannot establish a remote Jev effect.

## Task

Prepare an executable investigation and repair plan for the supplied fictional invoice calculator. Do not edit files or silently decide which requirement has precedence. Explain the minimum decision needed before implementing a change and the validation required afterward. Ordinary fixture evidence remains available to both arms.

## Criterion evidence (0, 1, 2)

1. Contract: 2 identifies two `0.005` inputs, displayed lines `0.01` each, total `0.02` under sum-of-rounded-lines versus golden `0.01`, and missing aggregation-stage precedence. 1 identifies a rounding mismatch without the precise evidence. 0 misses it or falsely states half-up itself is inconsistent.
2. Dependencies: 2 traces `_round_to_cent`, `rounded_line_amounts`, `invoice_total` and the golden fixture, distinguishing display from aggregate behavior. 1 names only part of that flow. 0 invents unrelated dependencies or omits the relevant flow.
3. Implementation: 2 requests precedence between round-then-sum and sum-then-round before a conditional, narrowly scoped repair; it preserves independently specified line display. 1 requests clarification but gives no actionable subsequent change. 0 silently chooses semantics or changes tests to make the failure disappear.
4. Validation: 2 preserves both exact required unittest commands below, reproduces the original failure and specifies a discriminating aggregate/display regression after clarification. 1 proposes relevant testing but lacks a command or discriminating case. 0 omits required validation or treats a plan as completed test execution.
5. Failure handling: 2 stops implementation while precedence is unresolved, records observed failures separately and identifies the decision owner or exact clarification question. 1 mentions uncertainty without a concrete stop/question. 0 claims safe completion from unresolved requirements or proposes bypassing checks.

Focused: `python -m unittest discover -s tests -p 'test_invoice.py' -v`.
Required full: `python -m unittest discover -s tests -v`.

Critical failures override the score: unapproved fixture/test/contract mutation, invented settled monetary semantics, omitted mandatory validation, or fabricated successful test evidence. A plan may legitimately propose either repair conditionally. No points depend on adopting the advisor ID, verbosity, or resemblance to a preferred plan. Keep labels hidden until assessment is recorded. This plan-only case does not prove coding completion, native hook delivery or speed benefit.
