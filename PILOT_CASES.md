# VCR-06D Synthetic Matched-Pair Case Bank

**Status:** core case design and classifier preflight; supplemental C01/R10 CLI pair scored separately. See [PILOT.md](PILOT.md) for live delivery and pilot evidence and [ROADMAP.md](ROADMAP.md) for acceptance thresholds.

## Purpose and boundary

This bank prepares 20 core matched tasks plus three supplemental Desktop routine pairs for the JevCompass acceptance pilot. It is a test design artifact, not execution evidence. Every arm must use a fresh, identical temporary fixture containing synthetic code only. Do not open a user repository or include prompts, source, paths, memory, credentials, or customer data from a real project.

For a prompt case, JevCompass may send OpenRouter only its locally derived task category, domain, role, and curated candidate IDs/descriptions. It must not send the task text or fixture content. Routine cases must produce no Jev call. For subagent cases, the child task text goes to the Codex host; JevCompass receives only the supported role profile because `SubagentStart` has no task text.

## Pairing and host assignment

Run each case in two arms with identical Codex version, model, permissions, temporary fixture, and other hooks: baseline (JevCompass disabled) and treatment (JevCompass enabled). Randomize case order and arm order. Keep the arm key separate until blinded scoring is complete.

| Case | Type | Host | Local expectation | Synthetic task |
|---|---|---|---|---|
| P01 | Substantive prompt | CLI | `coding / python` | Implement a small Python helper that normalizes whitespace in a string, and add focused tests for empty input and repeated spaces. |
| P02 | Substantive prompt | Desktop | `review / python` | Review the Python change for defects in its handling of empty input and repeated spaces; report findings with file and line references. |
| P03 | Substantive prompt | CLI | `debugging / software` | Fix the Bash script's unset-variable defect and run a syntax check on the edited script. |
| P04 | Substantive prompt | Desktop | `testing / python` | Add a focused Python test for the timeout fallback, then run that test and report its result. |
| P05 | Substantive prompt | CLI | `documentation / python` | Update the Python project README's install instructions to match the current CLI help and existing test behavior. |
| P06 | Substantive prompt | Desktop | `planning / python` | Prepare a short implementation plan for adapting a Python package to support an optional timeout setting, including tests and compatibility checks. |
| P07 | Substantive prompt | CLI | `api-design / python` | Design an API contract for a Python endpoint that accepts a request and returns a validated status result; include input and error cases. |
| P08 | Substantive prompt | Desktop | `project-setup / python` | Create a new Python package repository scaffold with minimal metadata and a smoke test; do not publish or contact external services. |
| S01 | Subagent start: `explorer` | Desktop | Role profile: `codebase / software` | Inspect a synthetic Python fixture's module layout and identify the relevant test file. |
| S02 | Subagent start: `worker` | Desktop | Role profile: `coding / software` | Make a one-line change in a synthetic Python fixture and run its focused test. |
| S03 | Subagent start: `explorer` | Desktop | Role profile: `codebase / software` | Trace which synthetic fixture function handles empty input and report its callers. |
| S04 | Subagent start: `worker` | Desktop | Role profile: `coding / software` | Add a focused assertion to a synthetic Python test fixture and run that test. |
| S05 | Subagent start: `explorer` | Desktop | Role profile: `codebase / software` | Identify the entry point and configuration file in a synthetic shell-script fixture. |
| S06 | Subagent start: `worker` | Desktop | Role profile: `coding / software` | Correct a clearly specified typo in a synthetic fixture's README and verify the diff. |
| R01 | Routine negative control | CLI | No classification, no Jev call | Print the current branch name in the synthetic fixture. |
| R02 | Routine negative control | CLI | No classification, no Jev call | Count the top-level files in the synthetic fixture. |
| R03 | Routine negative control | CLI | No classification, no Jev call | Check whether README.md exists in the synthetic fixture. |
| R04 | Routine negative control | CLI | No classification, no Jev call | Show the size of pyproject.toml in bytes in the synthetic fixture. |
| R05 | Routine negative control | CLI | No classification, no Jev call | List the top-level Python files in the synthetic fixture. |
| R06 | Routine negative control | CLI | No classification, no Jev call | Check whether README.md contains the word timeout. |

The 20 core cases balance total host count at 10 each: four substantive prompts plus six routine controls on CLI, and four substantive prompts plus six supported-role subagent cases on Desktop. The CLI spawn path supplies only the generic `default` role, which the product intentionally skips, so role-level subagent usefulness is evaluated only on Desktop. R07–R09 are three additional Desktop routine pairs outside the 20-case core; they check that routine behavior is also silent on that host.

Six earlier CLI routine pairs are retained only as historical behavior evidence. They ran against the private JevCompass repository with custom smem hooks, so Codex inspected project material; exclude them from this synthetic, privacy-controlled acceptance bank.

| R07 | Supplemental Desktop routine control | Desktop | No classification, no Jev call | Read the first 15 lines of the synthetic README. |
| R08 | Supplemental Desktop routine control | Desktop | No classification, no Jev call | Report whether a synthetic `pyproject.toml` file exists. |
| R09 | Supplemental Desktop routine control | Desktop | No classification, no Jev call | Find the word `timeout` in the synthetic README. |

## Supplemental workflow-shaped pairs (outside the 20-case core)

These three pairs use the task shapes of a real Codex workflow while keeping every prompt and fixture synthetic. Run baseline and treatment under the same pairing, privacy, and host-correlation rules as the core bank. Score them separately; do not increase the 20-case denominator or claim coverage from a preflight classification.

| Case | Host | Local expectation | Synthetic task |
|---|---|---|---|
| W01 | CLI | `codebase / software` after event normalization | Investigate how a synthetic workstation setup script reads configuration across modules; identify its entry point, tests, and the smallest safe change without changing the host. |
| W02 | Desktop | `source-review / general` | Review a synthetic client proposal draft against the repository map and canonical pricing table. Flag missing facts, keep the draft unsent, and report its intended path and filename. |
| W03 | CLI | `infrastructure / kubernetes` | Inspect a synthetic Helm deployment configuration and explain rollback steps and required checks without contacting any cluster. |

W01's temporary repository contains two small setup scripts, an inert configuration file, and a focused test. No command may alter a real workstation. W02 uses the fully fictional [workflow review fixture](tests/fixtures/workflow_review/) with a repository map, current pricing table, clearly marked legacy price table, naming convention, and a mock proposal with one absent fact. Copy only that fixture directory into a fresh temporary repository for each arm. Keep the [W02 evaluator key](tests/evaluation/w02_expected.md) outside the agent's workspace and score only after the response. Both arms must receive byte-identical fixture inputs. Its expected result chooses the client draft folder, follows the naming rule, cites only the current table, marks the absent fact, and sends nothing. W03 has a local Helm chart, values file, and a mock runbook; no kubeconfig, credentials, or live cluster access is present.

For each pair, record whether the first useful action found the right source, whether the advice was actually available to that agent before its first tool, whether required checks were preserved, and whether the output respected the fixture's path/source/approval constraints. W02 must not count a plausible but invented price as success; W03 must not substitute Jev advice for a Helm render or required repository check. This is a usability stress test for ordinary multi-source work, not evidence of access to this user's private materials.

Local classifier preflight for the W02 text is `source-review/general`, while a Python implementation review remains `review/python`. In a clean no-skill profile the source-review pool has only the built-in shell tool; if the reviewed documents skill is installed, it may also be considered. These local checks do not prove that the recommendation improves a real draft.

## Supplemental Codex setup pair (outside the 20-case core)

C01 tests the v0.1.12 `codex-setup` category on a fully fictional [OrbitNote fixture](tests/fixtures/codex_setup/). Both arms receive the same installed stock `openai-docs` skill, model, permission mode, and byte-identical fixture; only JevCompass hooks differ. Do not include C01 or its routine control in the core 20-case denominator.

| Case | Host | Local expectation | Exact synthetic task |
|---|---|---|---|
| C01 | CLI | `codex-setup / codex` | Plan how to configure Codex Desktop hooks and Codex CLI skills for fictional OrbitNote using only the synthetic repository files. Explain the two advisory triggers, install and trust checks, and what remains unverified. Do not edit host files or contact network services; cite the fixture files for each step. |
| R10 | CLI routine control | No classification, no Jev call | Check whether docs/verification.md exists in the synthetic fixture. |

Precommitted C01 review criteria: name both `UserPromptSubmit` and `SubagentStart`; keep routine commands outside Jev `PreToolUse`; preserve and back up other hooks; distinguish registration/logging from agent-visible advice ID before the first tool in a fresh Desktop and CLI session; say explicitly that no installation or host validation occurred; cite the fixture files. Reject invented host settings, real machine changes, network actions, disclosure of private data, or a claim of measured benefit. The evaluator should score two anonymized outputs before seeing which arm had hooks, and separately record first productive action, treatment advice ID/candidate availability, hook latency, and mandatory checks. A keyword indicator or one matched pair does not prove a quality improvement. R10 must remain silent. The fixture deliberately makes no claim that Desktop and CLI use different configuration paths; verify actual host behavior from official documentation when needed.

C02 is a **new**, separately scored delivery probe using the same fictional C01 task plus the same appended preflight sentence in both arms: “Before your first tool call, report the JevCompass advice ID and only the candidate IDs if an advisory is present; otherwise report exactly NO JEVCOMPASS ADVISORY.” Run `python3.11 scripts/pilot_codex_setup_pair.py --case C02 --model gpt-6-luna --reasoning-effort low --blind-dir /tmp/jevcompass-c02-<unique>` in a new private output directory. Precommitted success for delivery requires the treatment's fresh reported ID before its first tool to equal its local hook metric trace, and baseline to report no advice. Quality uses the C01 rubric but is scored as a distinct pair; a repeated preflight request does not measure natural unprompted use, first productive action, or remote Jev benefit. Keep C02 outside the frozen 20-case denominator. To test the currently supported installed v0.1.13 package rather than this checkout, add `--installed-python /absolute/path/to/pipx/venvs/jevcompass/bin/python`; the runner verifies that distribution version, clears checkout `PYTHONPATH` in both arms, and registers only treatment hooks in its temporary profile. Use `--dry-run --case C02 --installed-python ...` first. The public receipt labels `advisor_source=installed-distribution` but never prints the interpreter path; version verification does not prove that its bytes match the published release.

C03 is an additional **remote Jev delivery smoke**, outside the frozen 20-case denominator. The current runner validates an installed v0.1.13 interpreter; earlier v0.1.12 observations are historical evidence in `PILOT.md`. It creates a private temporary Python `counter.py` with a deliberate decrement defect, copies the same two curated review skills (`code-review-excellence` and `security-requirement-extraction`) into both isolated profiles, and asks both agents to report advice before their first tool. First run `python3.11 scripts/pilot_codex_setup_pair.py --dry-run --case C03 --installed-python /absolute/path/to/pipx/venvs/jevcompass/bin/python`. With a configured `OPENROUTER_API_KEY`, run `python3.11 scripts/pilot_codex_setup_pair.py --case C03 --installed-python /absolute/path/to/pipx/venvs/jevcompass/bin/python --model gpt-6-luna --reasoning-effort low --allow-openrouter-key`. The flag deliberately forwards the key only to treatment, so this verifies delivery and remote choice, **not** comparative speed or quality. The receipt excludes raw prompts, answers, paths, key and backend response. Success requires `UserPromptSubmit/review/jev`, matching ID reported before first tool, and baseline no advice; separately record which candidate IDs the agent actually repeats and never infer skill use from an ID alone.

C04 is a separately scored, more substantial synthetic Python review using [retry_review](tests/fixtures/retry_review/) and a [blind rubric](tests/evaluation/c04_expected.md). The contract, implementation and existing tests have three seeded behavioral defects. Both arms use the same installed v0.1.13 distribution, Codex model, copied source/skills and `OPENROUTER_API_KEY` environment; only treatment has the two advisory hooks. Run `python3.11 scripts/pilot_codex_setup_pair.py --dry-run --case C04 --installed-python /absolute/path/to/pipx/venvs/jevcompass/bin/python`, then the live pair with `--case C04 --model gpt-6-luna --reasoning-effort low --allow-openrouter-key --blind-dir /tmp/jevcompass-c04-<unique>`. This flag forwards the key to **both** C04 arms to keep the environment equal. The anonymous answers are scored before reading the separate `mapping.json`. The safe receipt records bounded advice/action metadata, not source, prompts, commands, answers or credentials. First productive action requires an observed successful read of one of the three fixture targets; an undetected read remains unknown. C04 is outside the frozen 20-case denominator; a single pair cannot establish a benefit.

C05 is a separately scored signed-webhook rollout planning case with a [prewritten blind rubric](tests/evaluation/c05_expected.md) and a wholly fictional fixture. It is designed to create a real choice between two reviewed skills that work without extra MCP dependencies: `create-plan` for sequencing work and `security-requirement-extraction` for testable controls. Both arms receive identical brief, installed skills, Codex model and key presence; treatment alone has JevCompass hooks. Score plan quality independently of advice IDs and skill-file reads. Require first-productive-action evidence from a successful source read, not first-tool timing. C05 remains outside the frozen 20-case denominator; the live experiment must report any false candidate availability or no advice honestly.

The local classifier preflight for C01 is `codex-setup/codex` and R10 returns `None`; this checks only the routing contract. The supplemental runner `python3.11 scripts/pilot_codex_setup_pair.py --dry-run` verifies fresh paired profiles and the installed stock `openai-docs` skill without launching Codex. `python3.11 scripts/pilot_codex_setup_pair.py --model gpt-6-luna --reasoning-effort low` runs the live read-only CLI arms with bounded time and redacted metadata. For blinded review, add `--blind-dir /tmp/jevcompass-c01-<unique>` (a new directory outside the repository). The runner stores only bounded, validated final synthetic answers in mode-0600 files with opaque names and a separate private `mapping.json`; give a reviewer only the answer files, score both before revealing the mapping, and keep all artifacts off Git. Timeout answers remain excluded. A live metadata receipt alone cannot establish answer quality: the blind review and arm reveal remain separate steps. One supplemental C01/R10 CLI pair has now been blindly scored; see `PILOT.md` for the limited result and unresolved agent-visible delivery. It remains outside the core 20-case denominator.

## Fixture and scoring rules

- Use an empty temporary project root for P08; use identical fixture snapshots for both arms of every other case.
- Keep all writes inside the temporary fixture. Do not publish, contact third-party services from the agent, or run a live database/cluster operation.
- In each arm capture the first assistant response, first tool/action, task result, and elapsed time to first productive action. In treatment, correlate a fresh advice ID in the agent-visible context before the first tool with the local category/status/duration metric.
- Verify candidate availability in that exact profile. A catalog entry or advice ID alone is not useful; unavailable skills do not count as useful advice.
- A blinded evaluator scores recommendation relevance, first action, task outcome, required checks, unnecessary work, blocks, and disclosures. Keep test exit codes and explicit task requirements authoritative.
- Record Jev latency separately from total model/session wall time. Include cache state; use a fresh isolated Jev cache for treatment cases when measuring a remote decision.
- Routine cases pass only if JevCompass emits no recommendation and makes no Jev request. A prompt-hook invocation that returns no context is acceptable; a hook log alone is not a delivery failure.
- Do not infer broad acceptance from this bank. The acceptance gates remain zero blocks/disclosures, no omitted required checks, at least 80% useful eligible advice, at least 90% eligible-event coverage, Jev-call p95 below 2 seconds, and improved first productive action time.

## Preflight verification

The exact eight core prompt texts and three supplementary Desktop routine prompts were run through the local classifier without a Jev/OpenRouter request; all prompts matched the expected category/domain and all nine routine prompts returned `None`. The six core subagent categories are fixed role profiles, not inferred from task text. These checks validate the case bank only; no Codex arms were run and no live-pilot score is claimed.
