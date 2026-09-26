# Frozen assessor contract: public money mapping

Candidate task: extend successful ParcelQuote JSON with money.currency USD and money.minor_units identical to integer total_cents, preserving all existing fields, calculation and validation. Baseline and treatment receive the same task and fixture.

Expected change scope: CLI serialization, not calculation or tests. Original unit checks pass; original public contract fails. A correct mapping makes the complete suite pass. Test-fixture guard verifies this in disposable copies.

Quality gates: preserve fields and integer totals; exact nested keys/currency; invalid input produces no JSON; frozen tests and contract unchanged; focused check and full required suite executed after edit. Independently verify final artifacts. Runtime unknown until pre-pair measurements; no injected sleeps. First useful failure may remain null after a correct first edit, and must not be invented. Separate blind artifact scoring from execution evidence.

This fixture establishes a plausible coverage tradeoff, not an observed benefit. Freeze its hash before running the pair; do not modify it based on model choice.
