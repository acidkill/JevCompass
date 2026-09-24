# JevCompass v0.1.8

JevCompass adds optional tool and skill advice to Codex Desktop and CLI. It uses two advisory hooks, `UserPromptSubmit` and `SubagentStart`, and never gates ordinary commands.

## Included

- A locally curated catalog of tools and installed skills, with availability checks and a small shortlist.
- Optional ranking through the OpenRouter Decisions API using Jev. The advisor sends coarse task and candidate metadata; it does not send raw prompts, code, diffs, or memory contents.
- `jevcompass install`, `doctor`, and explicit `recommend` commands.
- Faster hook startup for skipped tasks through lazy imports.

## Install for invited users

Download the wheel from this private release, verify its SHA-256 against the asset digest, then run:

```bash
pipx install ./jevcompass-0.1.8-py3-none-any.whl
jevcompass install
jevcompass doctor
```

Review the two new hooks in Codex `/hooks` and start a fresh session. Repository access is required to download the wheel. No PyPI package has been published.

## Verified scope

The 171-test suite passed on Linux CPython 3.11–3.14. The wheel and source archive passed content checks, and an isolated pipx install plus `doctor` passed. In a later fresh Codex CLI 0.155.1 session with the installed v0.1.8 wheel, a synthetic read-only task reported local advice ID `a14d8368` and the suggested `exec_command`/`git` IDs before its first tool. Four paired synthetic CLI tasks tied on task quality, so a productivity or quality gain is not established.

GitHub-hosted checks did not run because the account's billing/spending-limit state stopped jobs before runner steps. macOS runtime and current Desktop prompt delivery still need direct host tests. The CLI proof above used a local fallback, not a paid Jev request. Advice does not grant permissions, install skills, or replace required tests.
