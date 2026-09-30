# Changelog

JevCompass publishes a focused changelog per release. Evidence boundaries and pilot
limits live in [PILOT.md](PILOT.md); release-task receipts live in [TASKS.md](TASKS.md).
Earlier releases predate this file and are summarized in
[RELEASE_NOTES.md](RELEASE_NOTES.md) and the
[GitHub releases](https://github.com/acidkill/JevCompass/releases) page.

## 0.1.23 (2026-10-01)

This release publishes the three explicit, nonblocking coding-decision commands that
have been maturing on `main` since v0.1.22, together with the fifth bundled skill and
the typed-decision cache. Nothing in this release gates commands, runs tools, changes
permissions, or waives required validation, and no speed, quality, or cost benefit is
claimed: the paired-trial evidence to date remains mixed or censored
(see [PILOT.md](PILOT.md), VCR308/VCR312).

### Added — user-facing

- **Pretask strategy choice:** `jevcompass strategy choose --kind coding --signal …`
  returns up to two reviewed strategies from allowlisted signals only; it accepts no
  prompt, path, source, or log text. Verified local contract evidence
  (`--contract-evidence consistent|conflicting|absent|partial|unknown`) and explicitly
  resolved strategies (`--resolved-strategy`) route locally without a remote call.
  Dependency-change inspection advice includes a short local migration checklist.
- **Opt-in strategy hook:** `jevcompass install --strategy-advice` attaches conservative
  pretask guidance to `UserPromptSubmit`; `--disable-strategy-advice` removes it.
  Ordinary installs are unchanged and the hook has no evaluated coding benefit.
- **Post-change test order:** `jevcompass tests discover` finds Python focused checks
  from staged, unstaged, and untracked changes; `jevcompass tests rank` orders explicit
  or discovered candidates. It prints an order and never executes tests; the supplied
  required gate is returned unchanged. Optional verified metadata covers change
  signals, per-candidate coverage and runtime buckets, and coverage targets. Bounded
  `decision_reason` values distinguish local resolution, remote choice, and fallbacks.
- **Failure triage:** `jevcompass triage --exit-code … --kind … --hypothesis …` ranks a
  first diagnostic step for ambiguous failures using enums only. Verified observation
  facts (`--import-observation`, `--assertion-observation`, `--timeout-observation`)
  can resolve or constrain the choice locally and skip the remote call. Opt-in
  `--rank-hypotheses` adds a bounded pairwise causal ordering. Output includes a fixed
  `decision_reason` and per-step `selection_source` provenance.
- **Caller-verified diagnostic costs:** `--diagnostic-cost HYPOTHESIS=low|medium|high|unknown`
  passes fixed relative-effort tokens for supplied hypotheses only. Costs may guide
  next-check ordering; they are never treated as causal-likelihood evidence and never
  waive required checks.
- **Typed-decision cache (opt-in):** `JEVCOMPASS_TYPED_DECISION_CACHE=1` reuses
  validated strategy, test-order, and triage decisions for up to 24 hours with explicit
  `cache_hit`/provenance labels; disabled by default.
- **Fifth bundled skill:** the opt-in installer now ships
  `jevcompass-coding-workflow` alongside `jevcompass-focused-tests`,
  `jevcompass-regression-review`, `jevcompass-plan-implementation`, and
  `jevcompass-plan-cutover`. Skills remain explicit opt-in and never install silently.
- The `Changelog` project URL now points to this file.

### Added — repository trial tooling (not in the wheel)

- Paired-trial runners, supervisor triage bridges, case profiles, and immutable
  fixtures for pretask, test-order, and triage comparisons, including equal-arm
  mandatory-command exposure, safe shell-wrapper normalization, profile diagnostic
  costs wired end to end through the bridge, and observation flags ordered before
  `--rank-hypotheses` so intercepted commands stay bridge-counted.

### Fixed

- Triage separates causal hypotheses from diagnostic actions in pilot contracts.
- Coding intent is preserved across mandatory validation commands.
- Validation-timing status labels align with measured evidence.
- Safe simple shell wrappers are normalized without granting compound-command gate
  credit.

### Verification

- Full offline suite: 858 Python tests pass on Linux at this revision; the hosted CI
  job runs the same suite on Python 3.11 for the release pull request and again inside
  the publish workflow. Distribution contents are checked by
  `tools/check_distribution.py` in the publish workflow.
- Registry publication receipts (Trusted Publishing run, artifact digests, fresh
  install check) are recorded in [TASKS.md](TASKS.md), [PILOT.md](PILOT.md),
  [README.md](README.md), and [ROADMAP.md](ROADMAP.md) after publication.
- Unchanged boundaries: advice stays nonblocking; remote ranking sees only allowlisted
  coarse metadata; original failing exits and required validation are preserved;
  macOS runtime and fresh Desktop prompt delivery remain unverified; no repeatable
  speed or quality benefit is established.
