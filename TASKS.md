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
| VCR-05 | Complete cross-host acceptance and update product guidance | In progress | Fresh Desktop and CLI delivery checks, routine negative controls, and the paired pilot are recorded with advice IDs, first-tool ordering, usefulness, latency, and limitations; README and PILOT state match the evidence. |

## Latest task checkpoint (2026-09-24)

VCR-04 is complete: the official role contract is reflected in the matcher, runtime allowlist, docs, and privacy regression; 50 tests pass and Graphify rebuilt 225 nodes/392 edges. smem checkpoint persistence is deferred during the planned Surreal-Memory benchmark window; this task list and `PILOT.md` retain the local evidence, and no server investigation or changes were made.

## Acceptance guardrails

- Jev recommendations never grant permissions or block prompts, tools, or subagents.
- Classify prompt intent locally; send only approved coarse metadata and candidate descriptions to OpenRouter.
- Never send raw prompts, code, paths, memory contents, or user-specific skill text.
- A configured MCP entry is not evidence the active session exposes its functions.
- Advice names model-facing tools such as `exec_command`; tool hooks use Codex's canonical matcher name `Bash` for that shell channel.
- Required project instructions, validation, and authoritative test results remain in force.
- If a host does not provide sufficient event data, record the limitation and use the explicit `jevcompass recommend` command where appropriate.
