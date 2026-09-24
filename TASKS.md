# Vanilla Codex Readiness

## Delivery rule

Target Codex Desktop and CLI in a vanilla user setup, without relying on this repository's project instructions or custom MCPs. Keep recommendations optional and non-blocking. For every completed task: run its checks, record a concise smem checkpoint, make one focused commit on `main`, push it to `origin/main`, and verify the remote head.

## Task list

| ID | Task | Status | Exit checks |
|---|---|---|---|
| VCR-01 | Establish this delivery checklist and push cadence | Complete | Checklist is tracked on `main` and its commit is on `origin/main`. |
| VCR-02 | Make prompt advice relevant in vanilla sessions and name built-in tools as Codex exposes them | Complete | Substantive prompts can receive advice independent of unreliable permission-mode signals; trivial prompts skip; configured-only MCPs are excluded; built-in command recommendations use the actual Codex tool identifier; explanation, review, planning, and testing intents select separate catalog candidates; 45 unit tests pass; fresh CLI 0.155.1 smoke delivered `exec_command` before first tool (36.8 ms local fallback). |
| VCR-03 | Make the local catalog and doctor useful on an uncustomized Codex install | Complete | CODEX_HOME-aware skill/config discovery, installer, and privacy-safe aggregate doctor status; 50 unit tests pass. Isolated pipx local-source install and upgrade pass (0.1.0 → 0.1.1); doctor reads the isolated profile and withholds paths/model values. |
| VCR-04 | Tune subagent recommendations to metadata vanilla Codex actually provides | Complete | Official docs confirm built-in `default`, `worker`, and `explorer`; SubagentStart supplies role metadata but no task text. Only `worker`/`explorer` receive role-level advice; generic/custom types and transcript/session/project paths are skipped. 50 tests pass. |
| VCR-05 | Improve relevance of broad vanilla recommendations | Complete | API design and documentation have focused pools; planning, Kubernetes, and packaging skills are excluded from unrelated coding. 52 tests pass. Safe synthetic Jev spot-checks selected only `exec_command` for generic coding and `pytest` plus `python-testing-patterns` for Python testing; these do not count toward paired acceptance. |
| VCR-06 | Complete cross-host acceptance and update product guidance | In progress | Fresh Desktop and CLI delivery checks, routine negative controls, and the paired pilot are recorded with advice IDs, first-tool ordering, usefulness, latency, and limitations; README and PILOT state match the evidence. |
| VCR-06A | Verify fresh vanilla CLI prompt-hook delivery | Complete | In an isolated read-only Codex CLI 0.155.1 profile, the first agent message contained advice ID `c218487d` and `exec_command` before the first shell tool; local fallback metric was 6.44 ms. No remote key was present, so this is not remote Jev evidence. |
| VCR-06B | Verify remote Jev selection through a fresh CLI hook | Planned | Run with an explicitly configured OpenRouter key and safe synthetic metadata; record matching agent-visible advice ID, selected IDs, hook metric, and latency without sending a raw prompt to Jev. |
| VCR-06C | Reconfirm fresh Desktop advice delivery | Planned | In a fresh Desktop session, correlate the adapter trace with an ID and selected IDs stated by the agent before its first tool; keep prior single-smoke evidence separate. |
| VCR-06D | Score the 20-case paired pilot and decide acceptance | Planned | Complete randomized matched cases across prompt, subagent, and routine work; assess usefulness, eligible coverage, required-check completion, privacy, blocking, p95 Jev latency, and time to first useful action. |

## Latest task checkpoint (2026-09-24)

VCR-06A is complete: a fresh isolated vanilla CLI session delivered local fallback advice ID `c218487d` and `exec_command` in its first agent message before the first shell tool; the matching `UserPromptSubmit` metric was 6.44 ms. The remote OpenRouter key was absent, so remote hook selection is still untested. VCR-06B through VCR-06D remain planned; VCR-06 acceptance is open. VCR-05 and Graphify results remain recorded below in the commit history. smem checkpoint synchronization is deferred during the planned Surreal-Memory benchmark window; this task list and `PILOT.md` retain the local evidence, and no server investigation or changes were made.

## Acceptance guardrails

- Jev recommendations never grant permissions or block prompts, tools, or subagents.
- Classify prompt intent locally; send only approved coarse metadata and candidate descriptions to OpenRouter.
- Never send raw prompts, code, paths, memory contents, or user-specific skill text.
- A configured MCP entry is not evidence the active session exposes its functions.
- Advice names model-facing tools such as `exec_command`; tool hooks use Codex's canonical matcher name `Bash` for that shell channel.
- Required project instructions, validation, and authoritative test results remain in force.
- If a host does not provide sufficient event data, record the limitation and use the explicit `jevcompass recommend` command where appropriate.
