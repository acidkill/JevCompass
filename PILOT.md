# JevCompass acceptance pilot

## Status

**Not yet accepted.** Synthetic contract tests are useful but do not prove advice was delivered to an agent before its first tool choice, nor that advice improves live work.

## Required evidence

Run 20 paired, anonymized tasks in randomized order:

- 8 confirmed Plan mode prompts
- 6 supported subagent roles
- 6 routine tasks that should not trigger Jev

For each pair, record baseline/Jev arm, host and version, hook input mode/role, advice ID visible before first tool selection, first productive action time, recommendation usefulness, tool availability, required instruction/test coverage, blocks, data disclosures, and Jev duration. The evaluator should not know which arm used Jev.

## Acceptance thresholds

- Zero blocks or privacy disclosures
- No omitted mandatory instructions, review, or tests
- At least 80% of eligible recommendations marked useful
- At least 90% coverage of eligible events
- Jev-call p95 below 2 seconds
- Better time to first productive action on eligible tasks
- Routine tasks show no Jev calls or material latency regression

Score explicit jevcompass recommend trials separately from automatic hook coverage.

## Evidence table

No live paired study has been completed for this product repository yet. Add one row per task pair only after observing the actual host session. Do not treat configuration presence, a hook log, a direct script invocation, or a synthetic test as proof of agent delivery.
