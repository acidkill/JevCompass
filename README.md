# JevCompass

**Privacy-first tool and skill recommendations for Codex Desktop and CLI, powered by Jev.**

JevCompass helps Codex choose a small set of relevant tools and skills from a local, curated catalog. It adds two non-blocking advisor hooks: one for substantive user tasks in ordinary Codex sessions and one for supported subagent roles. It never gates everyday commands.

> The source repository remains private. TestPyPI distributions are publicly downloadable after an approved test release; PyPI is a separate release decision.

## What it does

- **Codex user prompts:** classifies substantive task intent locally in any permission mode, then asks Jev to choose when multiple reviewed candidates remain. API design and documentation use focused candidate pools; planning, Kubernetes, and packaging skills stay out of generic coding choices. A single specific candidate is suggested locally to avoid an unnecessary round trip; a generic `exec_command` singleton is suppressed as low signal. Short or single-step requests are skipped quickly; the hook does not infer the Codex Plan UI mode.
- **Codex subagents:** considers role-level suggestions for the built-in `explorer` and `worker` agents. The generic `default` role and custom agent types are skipped. If the only available candidate is Codex's generic shell tool, the hook stays silent. The [SubagentStart event](https://learn.chatgpt.com/docs/hooks) includes the agent type but not its task, so advice stays role-level and never reads a transcript.
- **Source reviews:** proposals and offers receive a separate local review path that reminds the agent to check authoritative sources, mark missing facts, and leave drafts unsent until explicitly authorized. Code reviews keep their own candidate pool. Jev sees only the generic task category and reviewed candidate metadata.
- **Local catalog:** checks reviewed skills against installed metadata and MCP integrations against local configuration. Namespaced plugin skills require a matching package identity; an unrelated standalone skill with the same basename is not treated as that plugin. `CODEX_HOME` selects the active Codex config and skill roots; `~/.agents/skills` remains discoverable. Private skill descriptions, integration names, configuration values, and paths are not sent to Jev.
- **Manual entry point:** `jevcompass recommend` uses explicit allowlisted metadata when the task is too brief or ambiguous for automatic classification.
- **Diagnostics:** `jevcompass doctor` reports the active config source, aggregate catalog counts, model/key status, hook registration, and whether hooks are explicitly disabled in the base Codex config, without printing local paths or configuration values. A missing OpenRouter key is reported but does not fail the overall check because local advice can work without it; an explicit live Jev test still requires a key. Doctor also reports the latest allowlisted local hook metric separately from registration; its age is the log file modification age, not a per-event timestamp. A metric confirms adapter execution on that host, but neither registration nor a metric proves hook trust in another session or advice visible to an agent before its first tool. The local choice-capacity examples show whether source review, codebase navigation, and Python coding have no candidate, a local shortlist, or at least two candidates of one kind that Jev could rank. Counts reflect the installed profile and PATH, including shell commands; they do not prove a candidate is callable in the active Codex session or that the advice helps.

A recommendation is optional guidance. Codex still follows project instructions, reads any selected skill, confirms actual tool availability, and runs required checks.

## Example

For a substantive repository setup task in an existing Git checkout with the `create-plan` skill installed, and without an OpenRouter key, the prompt hook may add this local shortlist (the ID is illustrative):

~~~text
JevCompass advice ID: 0123abcd
Local unranked fallback; Jev did not select these candidates. Optional tools and skills for this task; validate against the task and actual availability:
- tool `exec_command`: Codex built-in exec_command for bounded local shell commands
- local command `git`: Inspect repository changes and history (run through `exec_command`; confirm it is available in this session)
- skill `create-plan`: Create an implementation plan grounded in repository context
Configured MCP entries must be confirmed connected in this session. Read any chosen skill before use and follow required project instructions and tests. If you write a plan, include concise execution recommendations for the primary agent and useful subagents.
~~~

For shell work, `exec_command` is the model-facing Codex tool identifier. `Bash` is a separate canonical name used by tool-hook matchers. The exact result depends on the local catalog, the task category, and Jev's validated choice. When no API key is available or Jev cannot return a sufficiently confident choice, the hook provides an explicitly labeled, unranked local shortlist (up to three tools and three skills) instead of implying that Jev selected a winner. A single specific candidate is suggested locally without a Jev request; a singleton generic `exec_command` candidate is suppressed in both automatic hooks and explicit `recommend` output because it adds no task-specific guidance. Generic coding no longer pits memory capture and Git history against the shell as alternative first tools; those commands remain candidates in planning and review where they have a specific role. It never blocks the session.

The local catalog treats `exec_command` as Codex's built-in shell capability, enabled by default, and honors `[features] shell_tool = false`; it does not require `bash` in `PATH`. This is a configuration-derived signal. Invocation-level overrides, model restrictions, or host policy can still change the active tool set, so the agent confirms actual availability in the current session.

## Clean Codex profile

JevCompass can run without extra skills, MCP servers, or an OpenRouter key. A clean profile has few task-specific candidates: for example, a local `api-design/python` request may return "No recommendation available," while a `package-docs/python` request can suggest the built-in shell with a focused check of the package's actual help and tests. Silence means the catalog found no suitable verified option; it does not mean the Codex task failed. Use `jevcompass doctor` to inspect candidate capacity, and confirm any suggested tool in the active session. Installing a reviewed skill can add a candidate, but JevCompass does not install skills on the user's behalf.

## Requirements

- Python 3.11 or newer
- Codex Desktop or Codex CLI with the UserPromptSubmit and SubagentStart hook events
- `OPENROUTER_API_KEY` in the hook process environment, or the optional native system-keyring extra for remote selection

## Install

Authorized collaborators can download the wheel from the [private v0.1.7 alpha release](https://github.com/acidkill/JevCompass/releases/tag/v0.1.7) and install it with `pipx install ./jevcompass-0.1.7-py3-none-any.whl`. This release includes the VCR-115 hook startup fast path. On Linux CPython 3.14.7, 30 paired launches showed lower median startup time for both simple skips and eligible keyless hooks; p95 for eligible hooks varied, so this is bounded local timing evidence, not a general latency guarantee. Four fresh synthetic CLI pairs tied on task quality, so improvement remains unproven. Run `jevcompass install` and `jevcompass doctor` after installation. The repository and assets are not publicly accessible. Local Linux release checks and platform limitations are recorded in TASKS.md.

From a local checkout:

~~~bash
pipx install .
jevcompass install
jevcompass doctor
~~~

When replacing a wheel installed through `pipx` with a newer downloaded wheel, use `pipx uninstall jevcompass`, then `pipx install ./jevcompass-NEW_VERSION-py3-none-any.whl`. Reinject any optional extras (for example, `pipx inject jevcompass "keyring>=25"`), run `jevcompass doctor`, review changed hooks in `/hooks`, and start a fresh Codex session. This replacement path was verified with a digest-checked v0.1.5 wheel in the active Linux pipx profile: both runtime and pipx metadata reported 0.1.5, `doctor --json` passed, and `hooks.json` was unchanged. `pipx install --force` failed against the existing uv-backed environment on that host, so uninstall and reinstall were necessary. Confirm that the wheel digest matches the private release before installing it; verify optional injected extras and hook trust again afterward.

`jevcompass install` creates a timestamped backup before changing the active `hooks.json` (`$CODEX_HOME/hooks.json` when `CODEX_HOME` is set, otherwise `~/.codex/hooks.json`) and idempotently adds exactly the JevCompass `UserPromptSubmit` and `SubagentStart` registrations. It removes only known legacy Jev gate commands from `PreToolUse`; unrelated hooks, including smem, are preserved. No Node installation is needed. The installed hook command uses the pipx environment's Python interpreter so it keeps working after a shell restart.

Remote Jev selection uses `OPENROUTER_API_KEY` when present. For GUI-launched Desktop sessions that do not inherit a shell environment, install the optional secure-store extra and add the key interactively:

~~~bash
pipx inject jevcompass "keyring>=25"
jevcompass auth set
jevcompass auth status
env -u OPENROUTER_API_KEY jevcompass auth status
~~~

For a checkout install, use `pipx install ".[secure-store]"`. The final `env -u` check shows whether the keyring itself is configured even if your terminal already has an environment key. A terminal `doctor` that finds `OPENROUTER_API_KEY` cannot prove a GUI-launched Desktop hook inherited it. `auth set` hides the key while typing and sends it to the system keyring over the helper process's stdin; the key is never accepted as a command argument or written to JevCompass configuration. The hook checks the environment first, then queries only native macOS Keychain, Linux Secret Service/KWallet, or Windows Credential Locker backends in a short-lived process with a 350 ms timeout. An unavailable, unsupported, locked, or slow keyring is ignored and local advice continues. Keyring access can still trigger platform-specific authorization UI; configure and authorize the store interactively before relying on it in hooks. On macOS, Keychain access may authorize the pipx Python executable rather than only JevCompass, so review the system Keychain access controls for your needs. Third-party and plaintext backends are rejected. See the [Python keyring documentation](https://keyring.readthedocs.io/en/stable/) for platform setup and backend behavior. `auth status` and `doctor` report only the source and whether a key exists.

The default model is `typesafe/jev-1.13`; `JEVCOMPASS_MODEL` overrides it with another Decisions model identifier. After installation, open `/hooks`, review and trust each JevCompass entry, then start a fresh session. For example, `SubagentStart` can show `Installed 1`, `Active 0`, `Review 1` even when `jevcompass doctor` reports both registrations. Open that event, verify the JevCompass command and matcher, and trust that specific hook. A changed command or definition needs renewed trust; a running Desktop session may still use its earlier hook state, so test again after a fresh Desktop session. Confirm an advice ID in the agent's initial context before its first tool and correlate it with the safe local metric. A metric alone does not prove delivery. The installer does not create a credential or send a test request. `doctor` checks public model metadata without a paid decision and reports an inconclusive result without failing an otherwise healthy local setup; `doctor --test-jev` explicitly sends one synthetic, billed request. [OpenRouter Decisions API](https://openrouter.ai/docs/cookbook/building-agents/gate-tool-calls-with-jev).

To validate changes without writing files:

~~~bash
jevcompass install --dry-run
~~~

## Explicit advice

For a short or ambiguous task, request advice with explicit allowlisted metadata:

~~~bash
jevcompass recommend --category project-setup --domain software
jevcompass recommend --category debugging --domain python --role primary
jevcompass recommend --category project-setup --domain software --role planner
~~~

Allowed categories are infrastructure, debugging, testing, research, api-design, documentation, package-docs, coding, codebase, review, source-review, planning, operations, and project-setup. `package-docs` is reserved for Python package installation documentation; it adds a local check against project metadata, preserves the README's exact supported CLI invocation unless a replacement is run and verified, and distinguishes pre-existing test failures, with `python-packaging` suggested only when installed. Allowed domains are general, software, python, web, shell, kubernetes, and codex. Use the codex domain only for substantive Codex Desktop/CLI documentation or troubleshooting about hooks, settings, skills, models, setup, or related behavior. The command accepts no raw task prompt.

## Privacy boundaries

- Task classification happens locally. Jev receives only a category, domain, role, and a small set of generic catalog descriptions and selection criteria.
- The Decisions request body never contains the raw prompt, source code, repository paths, smem content, client data, or free-form model output. It carries only allowlisted task metadata and generic candidate descriptions. The OpenRouter API key is sent to OpenRouter only in the HTTPS authorization header; it is not included in the JSON body, cache, diagnostics, or logs.
- Installed skill frontmatter is inspected locally for discovery. Its private description is not copied into the Jev request or advice.
- An MCP server listed in `config.toml` is marked as configured, not proven connected. JevCompass excludes configured-only MCP entries from automatic advice, because hook input cannot prove the active tool connection. The catalog still reports them for inspection.
- Local commands such as `git` and `pytest` are invoked through Codex's `exec_command`, not exposed as separate Codex tools. Their presence on the hook process's `PATH` is only a local signal; confirm they work in the active session before relying on them. Git history advice is withheld unless the hook's current directory is a valid Git worktree; if a host starts hooks elsewhere, this may conservatively omit Git.
- Cache and diagnostic logs stay in the user's home directory. Cache keys and selections contain only allowlisted categories, roles, domains, catalog version, and candidate IDs. Eligible advice includes a fresh short random ID; the local metric records that ID with the event, category, outcome, and duration. Skipped simple prompts produce no metric unless temporary diagnostics are enabled.
- Match the advice ID in the agent's first response to the local metric to verify delivery. A log entry alone proves execution, not delivery. `JEV_ADVISOR_DIAGNOSTIC=1` additionally records sanitized hook input mode metadata for temporary troubleshooting.

Jev is an advisory service, not an authorization system or security boundary. Do not use this package to authorize actions or replace required tests, reviews, or human approvals.

## Commands

| Command | Purpose |
| --- | --- |
| jevcompass hook | Handle one Codex hook event from stdin; always fail open and exit without blocking. |
| jevcompass recommend | Request advice using a known category, domain, and role. |
| jevcompass doctor | Check Python, config source, OpenRouter key presence, public Decisions model metadata, catalog counts, local choice capacity, and hook registration. |
| jevcompass doctor --test-jev | Also send one synthetic, billed Jev decision request. |
| jevcompass install | Merge the two advisory hooks without installing Node. |
| jevcompass auth status | Show redacted environment/keyring credential status. |
| jevcompass auth set | Store an OpenRouter key through a hidden terminal prompt. |
| jevcompass auth delete | Remove the JevCompass system-keyring entry after confirmation. |

## Development and tests

~~~bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
PYTHONPATH=src python -m unittest discover -s tests -v
python -m build
~~~

The unit suite exercises the hook contract, allowlisted task classification, data minimization, uncertainty and timeout handling, cache validation, catalog availability, and idempotent installation on a clean temporary profile.

Current local test results must be checked in the working checkout; synthetic tests do not establish successful delivery in a live Codex Desktop or CLI session.

## Pilot status

The first alpha release uses four fresh synthetic CLI pairs (P01, P03, P05, P07) as a bounded release check. Blind task quality tied in all four, so improvement remains unproven. The stronger blinded 20-pair evaluation remains on the roadmap and must use substantive tasks across normal Codex permission modes; the previous Plan-only gate does not match vanilla Codex hook signals. Fresh-session delivery checks remain separate. Passing deterministic tests does not claim the thresholds have been met. Record host evidence and score paired baseline/Jev tasks in PILOT.md. Acceptance requires zero blocks and data disclosures, no omitted required checks, at least 80% useful advice, at least 90% coverage of eligible events, Jev-call p95 below 2 seconds, and improved time to first productive action. Score manual recommend trials separately.

## Soon available:

- Broader Desktop and CLI validation with blinded 20-case usefulness and speed measurements.
- Better task-specific test, error-triage, and priority recommendations after evidence from real workflows.
- A separate TestPyPI trial and wider distribution after release checks. See [ROADMAP.md](ROADMAP.md).

## GitHub positioning

Suggested description:

> Privacy-first tool and skill recommendations for Codex Desktop and CLI, powered by Jev.

Suggested topics: codex, codex-cli, ai-agents, agent-skills, mcp, tool-selection, developer-tools, jev.

Private repositories are not publicly searchable. Changing repository visibility and adding an open-source license require separate decisions.

## Test release

Build wheel and sdist, inspect both archives for private content, and test `pipx install` from the built wheel in a clean home directory. An approved manual `release.yml` workflow publishes to **public TestPyPI** through Trusted Publishing with short-lived OIDC credentials; install that version with `pipx install --index-url https://test.pypi.org/simple/ jevcompass` and run `jevcompass doctor`. Configure the TestPyPI trusted publisher for the exact private GitHub repository, workflow filename, and `testpypi` environment before running it. A production PyPI release is separate. [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/).
