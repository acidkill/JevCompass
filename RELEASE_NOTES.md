# JevCompass v0.1.10

JevCompass provides optional, non-blocking tool and skill advice for Codex Desktop and CLI. This invited-user release adds separate recent invocation status for each hook in `doctor` and repository-only synthetic delivery probes for prompt and subagent paths. These diagnostics do not establish agent-visible delivery or measured productivity gain.

**Release tag target SHA:** recorded in `TASKS.md` after committing and verifying the release artifacts.

## Install for invited users

Download `jevcompass-0.1.10-py3-none-any.whl` from the [private v0.1.10 GitHub release](https://github.com/acidkill/JevCompass/releases/tag/v0.1.10), verify its SHA-256 against GitHub's asset digest, then run:

```bash
pipx install ./jevcompass-0.1.10-py3-none-any.whl
jevcompass install
jevcompass doctor
```

Review and trust the two advisory hooks in Codex `/hooks`, then start a fresh session. Access to the private repository is required. No PyPI or TestPyPI package has been published.

## Verified scope

Linux CPython 3.11–3.14 test, packaging, artifact-content, isolated pipx install, and `doctor` results are recorded in `TASKS.md` for the tagged release commit. The isolated install checks the released wheel without modifying the active pipx environment or Codex hook configuration.

The latest GitHub CI run failed before any runner steps; its job arrays are empty, and GitHub annotated the run with failed recent account payments or a spending limit. The exact account-side cause is unverified. macOS runtime and current Desktop prompt delivery remain unverified. Recent subagent probes did not observe a child or receipt; that result is inconclusive about real delivery. Broader usefulness and a productivity or quality benefit are not established; previous synthetic CLI quality comparisons tied. Advice does not grant permissions, install skills, or replace required tests.
