# JevCompass v0.1.9

JevCompass provides optional, non-blocking tool and skill advice for Codex Desktop and CLI. This invited-user release updates packaging metadata and release documentation; it makes no claim of stable status or measured productivity gain.

**Application source commit:** `68ca0682edca5cf8f2a799ccaf0f0be4292bc6e0` (the implementation and launch-material baseline for this release).

## Install for invited users

Download `jevcompass-0.1.9-py3-none-any.whl` from the [private v0.1.9 GitHub release](https://github.com/acidkill/JevCompass/releases/tag/v0.1.9), verify its SHA-256 against GitHub's asset digest, then run:

```bash
pipx install ./jevcompass-0.1.9-py3-none-any.whl
jevcompass install
jevcompass doctor
```

Review and trust the two advisory hooks in Codex `/hooks`, then start a fresh session. Access to the private repository is required. No PyPI or TestPyPI package has been published.

## Verified scope

Linux CPython 3.11–3.14 test, packaging, artifact-content, isolated pipx install, and `doctor` results are recorded in `TASKS.md` for the tagged release commit. The active Linux pipx installation is updated to v0.1.9 and its hook-config hash is checked there.

CI jobs did not reach runner steps because of the account billing/spending-limit gate. macOS runtime and current Desktop prompt delivery remain unverified. Broader usefulness and a productivity or quality benefit are not established; previous synthetic CLI quality comparisons tied. Advice does not grant permissions, install skills, or replace required tests.
