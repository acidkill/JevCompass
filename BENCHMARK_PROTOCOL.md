# JevCompass three-use benchmark protocol

**Protocol version:** 1  
**Design frozen:** 2026-09-27  
**Status:** Design only. A companion JSON execution manifest is required before any run. No results are reported here.

## Question and scope

Evaluate JevCompass in the three use cases already represented in the reviewed pilot record: pretask strategy choice, post-change test order, and ambiguous failure triage. The comparison asks whether making the actual product advice available helps an agent complete the same task correctly and efficiently. A local suggestion, an accepted remote choice, and native Desktop delivery are different outcomes and will be reported separately.

This is a small descriptive benchmark. It does not claim statistical power, confidence, or broad acceptance. Prior mixed and negative observations remain part of the evidence and are not replaced by this cohort.

## Cohorts and allocation

First run **six feasibility pairs**: two pairs per use case. These check that the frozen tasks, arm parity, advice capture, tests, and independent validator work end to end. They are reported separately and never pooled with the main cohort. Feasibility results cannot be used to tune quality thresholds, select favorable fixtures, or change an advice rubric.

The main cohort is **20 pairs**: seven pretask, seven post-change test-order, and six ambiguous-triage pairs. This allocation, pair seeds, and first-arm orders are fixed in advance. If a use case cannot satisfy its predeclared eligibility or independent-validation gate, retain the unavailable slot and stop that slice; do not move its allocation to another use case.

Before the first run, create and freeze a companion JSON execution manifest. It must identify the reviewed fixture/case IDs and exact source, fixture, contract, test, and independent-validator hashes; the repository revision; model and reasoning settings; exact task/arm instructions, mandatory commands, and per-arm timeout; each scheduled pair's seed and order; installed-version identity and execution path; and enforceable total runtime plus spend/token ceilings. The manifest must be fixed before feasibility data is collected. If any listed artifact or cap is not available, do not start; record that as a readiness blocker. The source revision and runner implementation in the manifest must also verify the seed-to-order mapping in the tables below.

The feasibility schedule uses the runner's fixed order shuffle. Both pairs within each use case have opposite first arms.

| Pair | Use case | Seed | First arm |
|---|---|---:|---|
| P-F1 | Pretask strategy | 28101 | Baseline |
| P-F2 | Pretask strategy | 28104 | Treatment |
| T-F1 | Post-change test order | 28102 | Baseline |
| T-F2 | Post-change test order | 28106 | Treatment |
| A-F1 | Ambiguous failure triage | 28103 | Baseline |
| A-F2 | Ambiguous failure triage | 28107 | Treatment |

For the main cohort, run pairs in the order listed. “Baseline first” and “Treatment first” identify the first arm; the other arm follows.

| Pair | Use case | Seed | First arm |
|---|---|---:|---|
| P1 | Pretask strategy | 28001 | Baseline |
| T1 | Post-change test order | 28006 | Baseline |
| A1 | Ambiguous failure triage | 28018 | Baseline |
| P2 | Pretask strategy | 28004 | Treatment |
| T2 | Post-change test order | 28011 | Treatment |
| A2 | Ambiguous failure triage | 28015 | Treatment |
| P3 | Pretask strategy | 28002 | Baseline |
| T3 | Post-change test order | 28008 | Baseline |
| A3 | Ambiguous failure triage | 28019 | Baseline |
| P4 | Pretask strategy | 28007 | Treatment |
| T4 | Post-change test order | 28012 | Treatment |
| A4 | Ambiguous failure triage | 28016 | Treatment |
| P5 | Pretask strategy | 28003 | Baseline |
| T5 | Post-change test order | 28009 | Baseline |
| A5 | Ambiguous failure triage | 28020 | Baseline |
| P6 | Pretask strategy | 28010 | Treatment |
| T6 | Post-change test order | 28013 | Treatment |
| A6 | Ambiguous failure triage | 28017 | Treatment |
| P7 | Pretask strategy | 28005 | Baseline |
| T7 | Post-change test order | 28014 | Treatment |

These schedules give ten baseline-first and ten treatment-first main pairs overall, balanced within each use case as closely as possible. The current runner derives arm order by shuffling `["baseline", "treatment"]` with `random.Random(seed)`; the seed selects arm order only. It is not forwarded as an agent/model sampling seed. The manifest must verify this exact runner behavior and mapping against the source hash; if that contract changes, stop before running and freeze a corrected schedule. Record any separate model sampling seed only if the runtime exposes it.

## Paired arms

For each pair, use the same reviewed task instance, starting tree, model and reasoning settings, task facts, available tools, mandatory checks, and timeout. Keep the fixture and test contracts unchanged. The autonomous baseline receives the task and all task-relevant facts, with no Jev advice. The treatment receives those same facts and the actual applicable JevCompass advice. Advice is optional and nonbinding: the agent may adopt it, adapt it, decline it, or not act on it. Record that choice separately; never require adoption or count adoption itself as success.

Do not force a remote request when the product resolves the task locally or offers no eligible remote choice. Record the route taken. A local result is not an accepted remote choice; remote-choice outcomes are scored only when an eligible remote choice was actually returned and accepted. Keep local-route observations in their own stratum and do not pool them with remote-choice results.

For the three use cases:

- **Pretask strategy:** compare the agent's strategy and task outcome. Record local resolution, eligible remote response, acceptance, and resulting action as separate events.
- **Post-change test order:** compare the advised or autonomous initial test order against the frozen coverage rubric. Both arms still run every mandatory focused and full check.
- **Ambiguous failure triage:** preserve the same reproducible failing state in both arms. Score the selected diagnostic next step against the frozen independent rubric. A suggested next check is not a confirmed cause.

## Gates and recorded outcomes

Keep one record for every scheduled pair and both arms, including failed, timed-out, incomplete, and unavailable runs. Do not retry, replace, or rerun a pair to obtain a better result. Retain harness and provider failures in the record with their actual terminal status.

For each arm, preserve and score the predeclared gates: task/fixture identity and immutable hashes; required initial failure reproduction where the fixture specifies one; focused repair checks; full mandatory checks; and independent frozen correctness validation. Record each gate as pass, fail, skipped, or unavailable with its reason. A skipped or unavailable gate is not a pass. Do not run an altered frozen test or contract as trusted independent validation. Do not weaken gates after seeing a result.

Record, when actually available:

- preparation time, agent time, independent-validation time, and total validated completion time including preparation; distinguish a run that did not complete all gates;
- provider request latency and provider-reported usage/cost, if exposed; distinguish no request from a request whose billing is unknown;
- agent input, cached input, uncached-input estimate if reported, output tokens, and agent billing status; mark unavailable or unknown rather than estimate;
- immutable/source/fixture/test/contract hashes, exact focused/full commands and exit results, model/settings, route (local or remote), and whether advice was acknowledged before tools;
- optional advice adoption, rejection, or no action, without treating it as an outcome gate;
- first useful error only when the predeclared error marker is observed in the recorded run and its time can be measured. Otherwise mark it unobserved or unscored; do not infer usefulness from arbitrary text or from a failing exit code.

Report the three use cases separately, with all scheduled pairs and failure/missing counts visible. Show per-pair quality gates and descriptive completion-time differences only for arms that actually pass all required gates. Also show unsuccessful or incomplete outcomes; do not present a successful-only average. Do not make significance, confidence, power, or repeatable-benefit claims from this cohort.

## Product and environment evidence

Record the source revision, installed package/version identity, and execution path for every run. A source checkout or CLI run establishes only that path's behavior. It does not establish what an installed skill delivered to a native Codex Desktop session. Record native Desktop hook/advice evidence and installed-version identity as a separate acceptance result when available; if no such run is part of the cohort, state that native delivery remains unverified. Unit tests and offline fixture checks are supporting evidence, not agent-behavior or Desktop-acceptance evidence.

## Stop rules

The execution manifest must include the applicable per-arm timeout, a fixed total runtime limit, and a spend/token ceiling that the available controls can enforce or monitor. If billing is unknown, use the enforceable token/runtime ceiling and report billing as unknown. Do not start if no bounded limit can be established.

Stop the affected run or cohort immediately for a privacy/credential exposure, fixture or contract mutation, unequal arm facts, broken event capture, or invalid independent oracle. Stop when a predeclared time, token, or spend cap is reached. Keep all already scheduled outcomes and mark remaining slots not run; do not top up, reallocate, relax a threshold, or restart the cohort. Any protocol repair after feasibility must be documented and frozen before the main cohort starts, without using outcome quality to tune the decision rules.
