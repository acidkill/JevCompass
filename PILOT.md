# JevCompass acceptance pilot

## Status

**Not yet accepted.** Synthetic contract tests are useful but do not prove advice was delivered to an agent before its first tool choice, nor that advice improves live work.

## Synthetic runtime evidence (2026-09-23)

The installed Python client made one live, synthetic Decisions API request and received the expected `pytest` choice with confidence 1 in 540 ms. In a 20-case fixture run using a fresh cache per case, 12 of 13 eligible cases produced advice, all 7 routine cases stayed silent, and eligible-call p95 was 505 ms (maximum 509 ms). The remaining debugging case returned confidence below the local 0.5 threshold and was intentionally skipped. This is 92.3% synthetic coverage, not host delivery or blinded usefulness evidence.

`jevcompass doctor` passed after installation. Direct installed-hook invocation produced advice for both a Plan mode event and an explorer start. A fresh CLI ordinary prompt invoked the prompt hook and correctly received no Jev advice. A new Desktop subagent did not receive SubagentStart advice in its initial context; investigate hook reload, trust, matching, and host delivery before acceptance. The CLI Plan mode signal is also not confirmed.

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
