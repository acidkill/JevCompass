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
| VCR-06C | Record correlated Desktop named-role delivery smoke | Complete | One Desktop `explorer` child reported trace `968a1d2b`, `serena`, and `code-review-excellence` before its first tool. This is one named-role smoke, not fresh Desktop prompt-hook coverage or broad acceptance. |
| VCR-06D | Score the 20-case paired pilot and decide acceptance | Planned | Complete randomized matched cases across prompt, subagent, and routine work; assess usefulness, eligible coverage, required-check completion, privacy, blocking, p95 Jev latency, and time to first useful action. |
| VCR-06E | Evaluate task-aware spawn-hook feasibility for vanilla CLI | Complete | An isolated CLI 0.155.1 PreToolUse probe saw `collaborationspawn_agent`, but the `message` field was a 204-character Fernet-shaped string with no synthetic sentinel. The task cannot be classified safely or inserted into the child context through this hook, so no production PreToolUse hook was added. |
| VCR-06F | Verify Desktop UserPromptSubmit delivery in a fresh session | Planned | In a newly started Desktop session, capture an agent-visible advice ID and selected IDs before the first tool, then match the ID to a safe local hook metric; record whether selection used local fallback or remote Jev. |
| VCR-07 | Preserve useful local advice on a vanilla profile without an OpenRouter key | Complete | A blank-profile `project-setup` pool with `exec_command` and `git` now emits a clearly labeled unranked local shortlist when Jev cannot decide; confident per-kind Jev choices remain intact. Missing-key/API failure and low-confidence behavior are tested; the suite passes 53 tests and 36 subtests; isolated no-key smoke measured 0.42 ms with no blocking fields. |
| VCR-08 | Improve useful role-level advice for vanilla default subagents | Planned | Evaluate the built-in `default` role using only `agent_type`; add a concise, broadly useful profile only if evidence supports it, and verify no task text or private context is inferred. |
| VCR-09 | Make built-in command availability accurate across supported Codex hosts | Planned | Remove false negatives caused by assuming `bash` exists where Codex still exposes its built-in command tool; define and test availability semantics for hook and standalone CLI contexts. |
| VCR-10 | Review OpenRouter's Jev permission-approval integration pattern | Complete | Record what can transfer safely: permission-time triggering, deterministic local risk exclusions, typed judgments, strict allow threshold, and fail-safe return to Codex's normal prompt. Document raw-data and hook-key limitations. |
| VCR-11 | Prototype an opt-in Jev-assisted Codex permission flow | Planned | Separately evaluate a disabled-by-default `PermissionRequest` mode with a tested key source, narrow eligible command policy, no raw prompt/code/path disclosure, unchanged host deny rules, and normal approval on every uncertain/error case. Never restore broad `PreToolUse`. |

## Latest task checkpoint (2026-09-24)

VCR-06A is complete: isolated vanilla CLI local advice ID `c218487d` and `exec_command` arrived before the first shell tool (6.44 ms). VCR-06C is complete for one correlated Desktop `explorer` smoke: trace `968a1d2b`, `serena`, and `code-review-excellence` were reported before the first tool; a fresh Desktop prompt-hook test is separately tracked as VCR-06F. VCR-06E found no safe task text in the spawn pre-hook, so no third production hook was added. VCR-06B, VCR-06D, and VCR-06F remain planned; remote Jev hook selection, fresh Desktop prompt delivery, and the 20-case acceptance pilot remain open. VCR-05 and Graphify results remain in prior checkpoints. smem checkpoint synchronization is deferred during the planned Surreal-Memory benchmark window; this task list and `PILOT.md` retain the local evidence, and no server investigation or changes were made.

## Vanilla usability backlog (2026-09-24)

A read-only catalog audit identified three product gaps: keyless profiles silently lost ambiguous multi-candidate recommendations; the built-in `default` subagent role receives no role-level guidance; and shell-based availability probing may hide Codex's built-in command tool on hosts without `bash`. VCR-07 is complete; VCR-08 and VCR-09 remain planned. VCR-07's isolated keyless smoke returned `exec_command` and `git` as an unranked local list (0.42 ms); 53 tests and 36 subtests passed. This synthetic result does not establish live agent delivery. VCR-06's fresh Desktop prompt test, remote Jev delivery test, and paired acceptance pilot remain separate acceptance work and are not implied complete by these improvements.

## Jev permission-flow article review (2026-09-24)

Reviewed [OpenRouter's Jev permission-approval recipe](https://openrouter.darenbot.com/docs/cookbook/coding-agents/auto-approve-permission-prompts-with-jev) against [Codex hook documentation](https://learn.chatgpt.com/docs/hooks). Transferable pattern: evaluate only a real `PermissionRequest`, run local deterministic exclusions before Jev, use typed bounded judgments, allow only after every required score clears a validated threshold, and leave the original Codex prompt untouched on uncertainty, timeout, or API failure. Codex's `PermissionRequest` runs only when approval is about to be requested; if no hook decides, Codex continues its normal approval flow.

Important limits: the Codex event has no reliable task objective; the article therefore drops task-fit and asks only about reversibility. The recipe sends command text and project path to OpenRouter and reports that its hook did not receive `OPENROUTER_API_KEY` in a CLI 0.155.1 capture; its `.env` workaround is not adopted here. A future permission mode must solve key delivery safely, avoid raw prompts/code/paths, preserve Codex deny rules, keep high-risk actions on the normal prompt, and remain disabled unless a user explicitly enables that distinct capability. Its quoted latency, accuracy, and cost figures are the article author's measurements, not JevCompass results.

## Acceptance guardrails

- Jev recommendations never grant permissions or block prompts, tools, or subagents.
- Classify prompt intent locally; send only approved coarse metadata and candidate descriptions to OpenRouter.
- Never send raw prompts, code, paths, memory contents, or user-specific skill text.
- A configured MCP entry is not evidence the active session exposes its functions.
- Advice names model-facing tools such as `exec_command`; tool hooks use Codex's canonical matcher name `Bash` for that shell channel.
- Required project instructions, validation, and authoritative test results remain in force.
- If a host does not provide sufficient event data, record the limitation and use the explicit `jevcompass recommend` command where appropriate.
