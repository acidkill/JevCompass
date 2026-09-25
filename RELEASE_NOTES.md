# JevCompass v0.1.10

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

GitHub Actions run [36087868425](https://github.com/acidkill/JevCompass/actions/runs/36087868425) failed before runner steps; job step arrays were empty and GitHub attached a payment-or-spending-limit annotation. The private repository's Actions budget screen showed a $0 cap with spending stopped; hosted jobs did not start. Public hosted CI must be checked after the visibility change. macOS runtime and current Desktop prompt delivery remain unverified. Recent subagent probes did not observe a child or receipt; that result is inconclusive about real delivery. Broader usefulness and a productivity or quality benefit are not established; previous synthetic CLI quality comparisons tied. Advice does not grant permissions, install skills, or replace required tests.
