# Mixed test-order evaluation protocol

## VCR-256-B: eligibility audit, 2026-09-27

The existing ParcelQuote partial-kilogram fixture is unsuitable for testing the new mixed-tradeoff metadata. Both `test_unit_quote.py::test_partial_kilogram_rounds_up` and `test_contract_cli.py::test_partial_kilogram_json_contract` directly exercise the same changed calculation. A ten-run local subprocess measurement before any repair gave unit median 49.198 ms (47.157–51.222) and contract median 133.804 ms (129.406–138.676). Both commands consistently exited 1 on the seeded defect. These measurements include interpreter startup, are host-specific and are not agent task-completion measurements. They do not justify a universal fast/slow threshold. The unit suite offers the same relevant defect detection with lower measured runtime: do not invent an indirect coverage label to force a remote decision.

## Next case, fixed before selection

Use a boundary-mapping change: preserve the shipping calculation while extending the existing JSON interface with a explicitly specified public representation. Both arms receive byte-identical contract, source, test options and task prompt. Direct CLI contract checks cover the changed mapping; a cheaper unit suite exercises the unchanged calculation and provides indirect regression coverage. Verify this relation by reading assertions and the mapping call path. Measure both suites locally before any remote choice; publish the measurements, bucket policy and fixture hash before the pair. If the measured distinction is unstable, keep runtime unknown. Do not add sleeps, large irrelevant loops or artificial external dependencies.

This leaves a legitimate direct/slower versus indirect/faster tradeoff. A baseline agent can reasonably choose direct coverage without assistance. Agreement does not establish benefit; abstention and treatment overhead remain valid negative outcomes.

## Equal-information and outcome gates

- Randomize arm order with a recorded seed; pin the same Codex version, model, reasoning effort, permissions and authentication.
- Expose the same optional and mandatory commands and measured metadata to both arms. Only treatment receives the instruction to call the advisor. Record the prompt difference as a confound.
- Keep assessor expectations outside the candidate workspace. Freeze the public contract and expected outputs before running either arm.
- Verify a meaningful public-interface change and preserve existing input validation and calculation. Never edit the contract or tests to make the outcome pass.
- Require one focused suite after the source change and the complete mandatory suite; independently run both suites against final artifacts. Distinguish agent execution evidence from external verification.
- Measure completion wall time, actual first relevant failure (null if none), first successful relevant check and advice latency. A tool-start timestamp alone is not useful-action evidence.
- Retain Codex input/cached/output counters; bill remains unknown unless authoritative billing evidence exists. Retain validated Decisions API tokens and cost even for abstention; never substitute zero for unknown usage.
- Export anonymized final artifacts for a reviewer before revealing variants. Score correctness and validation separately; leave transcript-dependent dimensions unscored when unavailable.
- Publish both arms, including invalid runs and abstentions. Do not tune the fixture after seeing the model's selection. No claim of repeatable benefit from one pair.

## Prerequisites still open

The test-order result and CLI now retain validated Decisions API usage, including abstentions. Wire numeric-only receipts into the matched runner before the live pair; missing usage and cost must remain unknown. The current runner is specific to the partial-kilogram calculation and must be adapted to the new boundary case with targeted tests. Native Desktop comparison remains separate from CLI evidence.
