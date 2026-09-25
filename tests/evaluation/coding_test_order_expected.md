# Coding test-order fixture: frozen evaluator notes

Keep this file outside the candidate-visible fixture directory. It records the
benchmark target and expected outcomes; do not provide it to benchmark agents.

## Frozen task

Candidate prompt: “Fix ParcelQuote's shipping quote calculation so every
positive partial kilogram is charged as a whole started kilogram. Exact
kilogram weights must keep their current price. Preserve input validation and
the existing JSON command-line interface.”

## Required command and optional candidates

- Required full suite: `python -m unittest discover -s tests -v`
- Optional unit candidate: `python -m unittest discover -s tests -p 'test_unit*.py' -v`
- Optional contract/integration candidate: `python -m unittest discover -s tests -p 'test_contract*.py' -v`

The required suite includes both test kinds. They are also useful independent
optional checks: the unit suite calls the pricing function directly, while the
contract suite starts a fresh CLI process and validates the public JSON and
error behavior.

## Seed and expected behavior

Seeded defect is `weight_grams // 1000` in
`parcelquote/quote.py::quote_shipping`, which ignores a positive remainder.
The focused unit case `test_partial_kilogram_rounds_up` fails immediately on the
seed and expects 1001 grams to bill as 2 kilograms / 500 cents in the near zone.
The CLI contract case for the same input independently expects the public JSON
to report those values. Correct behavior is positive integer ceiling division,
`(weight_grams + 999) // 1000`, after existing validation.

## Frozen scoring rubric (10 points)

- 4 points: every positive partial kilogram rounds up; exact multiples remain
  unchanged, including the smallest valid weight.
- 2 points: near/far base charges and per-started-kilogram arithmetic remain
  correct.
- 2 points: CLI success JSON shape and values are preserved.
- 1 point: invalid weights/zones remain rejected without successful JSON.
- 1 point: focused and full unittest suites pass without new dependencies or
  unrelated changes.

## Fixture validation baseline

Before the reference fix, the focused unit test and the 1001-gram CLI contract
test fail because the fixture reports one billable kilogram / 375 cents. In an
ephemeral reference-fixed copy, both optional suites and the complete required
suite pass. Timings are intentionally not part of the rubric.
