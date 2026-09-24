# JevCompass

**Privacy-first tool and skill recommendations for Codex Desktop and CLI, powered by Jev.**

JevCompass helps Codex choose a small set of relevant tools and skills from a local, curated catalog. It adds two non-blocking advisor hooks: one for substantive user tasks in ordinary Codex sessions and one for supported subagent roles. It never gates everyday commands.

> The source repository remains private. TestPyPI distributions are publicly downloadable after an approved test release; PyPI is a separate release decision.

## What it does

- **Codex user prompts:** classifies substantive task intent locally in any permission mode, then asks Jev to choose when multiple reviewed candidates remain. A single clear candidate is suggested locally to avoid an unnecessary round trip. Short or single-step requests are skipped quickly; the hook does not infer the Codex Plan UI mode.
- **Codex subagents:** provides role-level suggestions for explorer, worker, and luna_worker. The SubagentStart event does not include the task prompt, so advice is role-based only.
- **Local catalog:** checks reviewed skills against installed skill names and MCP integrations against local configuration. `CODEX_HOME` selects the active Codex config and skill roots; `~/.agents/skills` remains discoverable. Private skill descriptions, integration names, configuration values, and paths are not sent to Jev.
- **Manual entry point:** `jevcompass recommend` uses explicit allowlisted metadata when the task is too brief or ambiguous for automatic classification.
- **Diagnostics:** `jevcompass doctor` reports the active config source, aggregate catalog counts, model/key status, and hook registration without printing local paths or configuration values.

A recommendation is optional guidance. Codex still follows project instructions, reads any selected skill, confirms actual tool availability, and runs required checks.

## Example

For a substantive coding task, the prompt hook may add:

~~~text
JevCompass advice ID: 0123abcd
Optional tools and skills for this task; validate against the task and actual availability:
- tool `exec_command` — Codex's built-in shell tool for bounded local checks and repository inspection
- skill: python-packaging — Build and distribute a Python package with modern project metadata

Configured MCP entries must be confirmed connected in this session. Read any chosen skill before use and follow required project instructions and tests. If you write a plan, include concise execution recommendations for the primary agent and useful subagents.
~~~

For shell work, `exec_command` is the model-facing Codex tool identifier. `Bash` is a separate canonical name used by tool-hook matchers. The exact result depends on the local catalog, the task category, and Jev's validated choice. A missing backend, timeout, uncertain result, or malformed response produces no recommendation and does not block the session.

## Requirements

- Python 3.11 or newer
- Codex Desktop or Codex CLI with the UserPromptSubmit and SubagentStart hook events
- `OPENROUTER_API_KEY` in the hook process environment for remote selection

## Install

From a local checkout:

~~~bash
pipx install .
jevcompass install
jevcompass doctor
~~~

`jevcompass install` creates a timestamped backup before changing the active `hooks.json` (`$CODEX_HOME/hooks.json` when `CODEX_HOME` is set, otherwise `~/.codex/hooks.json`) and idempotently adds exactly the JevCompass `UserPromptSubmit` and `SubagentStart` registrations. It removes only known legacy Jev gate commands from `PreToolUse`; unrelated hooks, including smem, are preserved. No Node installation is needed. The installed hook command uses the pipx environment's Python interpreter so it keeps working after a shell restart.

Set `OPENROUTER_API_KEY` for the Codex process before starting Desktop or CLI. The default model is `typesafe/jev-1.13`; `JEVCOMPASS_MODEL` overrides it with another Decisions model identifier. After installation, review and trust the hooks in Codex with `/hooks`, then start a fresh session. The installer does not create a credential or send a test request. `doctor` checks public model metadata without a paid decision; `doctor --test-jev` explicitly sends one synthetic, billed request. [OpenRouter Decisions API](https://openrouter.ai/docs/cookbook/building-agents/gate-tool-calls-with-jev).

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

Allowed categories are infrastructure, debugging, testing, research, documentation, coding, codebase, review, planning, operations, and project-setup. Allowed domains are general, software, python, web, and kubernetes. The command accepts no raw task prompt.

## Privacy boundaries

- Task classification happens locally. Jev receives only a category, domain, role, and a small set of generic catalog descriptions and selection criteria.
- JevCompass does not send the raw prompt, source code, repository paths, smem content, client data, credentials, or free-form model output.
- Installed skill frontmatter is inspected locally for discovery. Its private description is not copied into the Jev request or advice.
- An MCP server listed in `config.toml` is marked as configured, not proven connected. JevCompass excludes configured-only MCP entries from automatic advice, because hook input cannot prove the active tool connection. The catalog still reports them for inspection.
- Cache and diagnostic logs stay in the user's home directory. Cache keys and selections contain only allowlisted categories, roles, domains, catalog version, and candidate IDs. Eligible advice includes a fresh short random ID; the local metric records that ID with the event, category, outcome, and duration. Skipped simple prompts produce no metric unless temporary diagnostics are enabled.
- Match the advice ID in the agent's first response to the local metric to verify delivery. A log entry alone proves execution, not delivery. `JEV_ADVISOR_DIAGNOSTIC=1` additionally records sanitized hook input mode metadata for temporary troubleshooting.

Jev is an advisory service, not an authorization system or security boundary. Do not use this package to authorize actions or replace required tests, reviews, or human approvals.

## Commands

| Command | Purpose |
| --- | --- |
| jevcompass hook | Handle one Codex hook event from stdin; always fail open and exit without blocking. |
| jevcompass recommend | Request advice using a known category, domain, and role. |
| jevcompass doctor | Check Python, config source, OpenRouter key presence, public Decisions model metadata, catalog counts, and hook registration. |
| jevcompass doctor --test-jev | Also send one synthetic, billed Jev decision request. |
| jevcompass install | Merge the two advisory hooks without installing Node. |

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

The blinded 20-pair evaluation must be rerun with substantive tasks across normal Codex permission modes; the previous Plan-only gate does not match vanilla Codex hook signals. Fresh-session delivery checks remain separate. Passing deterministic tests does not claim the thresholds have been met. Record host evidence and score paired baseline/Jev tasks in PILOT.md. Acceptance requires zero blocks and data disclosures, no omitted required checks, at least 80% useful advice, at least 90% coverage of eligible events, Jev-call p95 below 2 seconds, and improved time to first productive action. Score manual recommend trials separately.

## GitHub positioning

Suggested description:

> Privacy-first tool and skill recommendations for Codex Desktop and CLI, powered by Jev.

Suggested topics: codex, codex-cli, ai-agents, agent-skills, mcp, tool-selection, developer-tools, jev.

Private repositories are not publicly searchable. Changing repository visibility and adding an open-source license require separate decisions.

## Test release

Build wheel and sdist, inspect both archives for private content, and test `pipx install` from the built wheel in a clean home directory. An approved manual `release.yml` workflow publishes to **public TestPyPI** through Trusted Publishing with short-lived OIDC credentials; install that version with `pipx install --index-url https://test.pypi.org/simple/ jevcompass` and run `jevcompass doctor`. Configure the TestPyPI trusted publisher for the exact private GitHub repository, workflow filename, and `testpypi` environment before running it. A production PyPI release is separate. [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/).
