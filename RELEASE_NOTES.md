# JevCompass v0.1.11

This public GitHub release packages the current source onboarding and the `history` advice category alongside the two non-blocking Codex hooks, explicit recommendations, and optional OpenRouter Decisions ranking. It fixes text `doctor` showing an old package version and adds `jevcompass --version`. The first-screen README now shows a real keyless local example and a way to check agent-visible advice in a fresh session.

The source tag peels to commit `1e71eee5e3f53e52414aa63ffce72eb67a14edb2`. Hosted PR #9 passed 179 Python 3.11 tests; the same 179 tests passed locally on Python 3.11 and 3.14. Clean wheel/sdist auditing, isolated pipx install, and independent GitHub download/digest checks passed. Wheel SHA-256: `b7ffba2547ef457a4aa9f288afb7d612763895490811c25fe29108619c27c3ad`; sdist SHA-256: `124695b004b0975cc90626a8cf735cc5e207bde9969cd2ff1c4e525fc75da396`. This release does not claim macOS validation, universal hook delivery, measurable speed or quality improvement, or publication on PyPI. The repository and this release are licensed under [Apache License 2.0](LICENSE).

## Previous release: v0.1.10

JevCompass provides optional, non-blocking tool and skill advice for Codex Desktop and CLI. The v0.1.10 release adds separate recent invocation status for each hook in `doctor` and repository-only synthetic delivery probes for prompt and subagent paths. These diagnostics do not establish agent-visible delivery or measured productivity gain.

**Tag target SHA:** `27d2bfde9ade1272d3aaa3f98e1b651fb2ee79bc` (verified in the VCR-140 row in [TASKS.md](TASKS.md#2026-09-25-documentation-accuracy)). Current main has since advanced; the release wheel remains the tagged v0.1.10 artifact.

## Install

See the [README](README.md) for the current quick start, [PILOT.md](PILOT.md) for acceptance evidence, and [TASKS.md](TASKS.md) for this release's receipts and open gates. The v0.1.10 wheel does not include the `history` category added to current source in commit `2c95c3a`.

Download `jevcompass-0.1.10-py3-none-any.whl` from the [v0.1.10 GitHub release](https://github.com/acidkill/JevCompass/releases/tag/v0.1.10), verify its SHA-256 against GitHub's asset digest, then run:

```bash
pipx install ./jevcompass-0.1.10-py3-none-any.whl
jevcompass install
jevcompass doctor
```

Review and trust the two advisory hooks in Codex `/hooks`, then start a fresh session. No PyPI or TestPyPI package has been published.

## Verified scope

Linux CPython 3.11–3.14 test, packaging, artifact-content, isolated pipx install, and `doctor` results are recorded in `TASKS.md` for the tagged release commit. The isolated install checks the released wheel without modifying the active pipx environment or Codex hook configuration.

GitHub Actions run [36087868425](https://github.com/acidkill/JevCompass/actions/runs/36087868425) failed before runner steps; job step arrays were empty and GitHub attached a payment-or-spending-limit annotation. The private repository's Actions budget screen showed a $0 cap with spending stopped; hosted jobs did not start. After the repository was made public, the later [PR #3 CI run](https://github.com/acidkill/JevCompass/actions/runs/36090989287) passed all 177 Python 3.11 tests; this result applies to the later public source commit, not the tagged release. macOS runtime and current Desktop prompt delivery remain unverified. Recent subagent probes did not observe a child or receipt; that result is inconclusive about real delivery. Broader usefulness and a productivity or quality benefit are not established; previous synthetic CLI quality comparisons tied. Advice does not grant permissions, install skills, or replace required tests.
