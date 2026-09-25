# JevCompass

![A compass choosing a path among developer tools and skills](assets/jevcompass-hero.png)

**Give Codex a map of the tools and skills it already has.**

A useful skill can be buried in your setup. JevCompass discovers installed candidates locally, narrows them with a reviewed catalog, and offers a short suggestion when a task has a clear match. It works with Codex Desktop and CLI hooks, without replacing your agent or gating everyday commands.

- **Start fast:** try explicit advice from the terminal before installing any hooks.
- **Keep control:** suggestions never run tools, grant permissions, or override required checks.
- **Choose your privacy level:** local advice works without a key; optional Jev ranking sees only coarse candidate metadata through OpenRouter.

[Get started](#quick-start) · [See real output](#60-second-demo) · [Understand the boundary](#privacy) · [Check the evidence](#what-has-been-verified) · [Contribute](#contribute)

## Quick start

Requirements: Python 3.11 or newer, [pipx](https://pipx.pypa.io/stable/installation/), and a Codex installation with hooks enabled. Install the [published package on PyPI](https://pypi.org/project/jevcompass/):

```bash
pipx install jevcompass
jevcompass recommend --category review --domain python
```

To install from source instead, clone this repository and run `pipx install .` from the checkout. For a reproducible version, use `pipx install jevcompass==0.1.15`. The latest source on `main` may contain unreleased changes.

The recommendation is environment-dependent: it can show local advice, an unranked shortlist, or no recommendation. The manual command uses explicit category/domain metadata and never takes a task prompt.

To enable automatic advice, first inspect what would change, then install:

```bash
jevcompass install --dry-run
jevcompass install
jevcompass doctor
```

**What changes:** `jevcompass install` backs up an existing Codex `hooks.json` and registers two advisory hooks. Review and trust the registrations in Codex's `/hooks` screen, then start a fresh session. The default config is under `~/.codex`; set `CODEX_HOME` to use a different Codex home. A successful `doctor` check confirms local registration, not that Codex loaded or delivered advice.

## Optional skills for everyday coding

A plain Codex profile may have no reviewed skills to recommend. To add two small, local workflows for focused tests and regression review, opt in explicitly:

```bash
jevcompass skills install --dry-run
jevcompass skills install
jevcompass doctor
```

The installer copies only `jevcompass-focused-tests` and `jevcompass-regression-review` into your Codex skill directory. With the default profile it uses `~/.agents/skills`; with `CODEX_HOME` set it uses that profile's `skills/` directory. It never changes hooks, executes scripts, calls Jev, or overwrites a different skill with the same name. Identical repeats are no-ops. Read the bundled `SKILL.md` files before enabling them, and start a fresh Codex session if they do not appear. Remove those two directories yourself after checking their contents if you no longer want the skills.

These skills provide task guidance, not test results or a guarantee of fewer regressions. An isolated local check confirmed that the coding and review candidate lists include them after installation; agent use and outcome benefit remain to be measured.

## Verify advice in a fresh session

After `jevcompass install`, inspect the two registrations in Codex `/hooks`, trust them if prompted, and start a **new** Desktop or CLI session. For a focused check, give Codex a substantive task such as planning a small Python package and ask it to report any `JevCompass advice ID` and suggested IDs from its initial context **before its first tool call**. An absent ID means that session did not receive advice; `doctor` and local hook metrics alone cannot prove delivery. The advisor may intentionally stay silent when only a generic shell command is available.

If your Codex host does not load either hook, run `jevcompass doctor --json` to distinguish registration from recent invocation, and use the explicit `jevcompass recommend --category project-setup --domain python` command while investigating. Never treat a manual result as proof that a hook delivered context to an agent.

## 60-second demo

After the quick start, ask about setting up a Python project:

```bash
jevcompass recommend --category project-setup --domain python
```

For example, on a Linux checkout with `OPENROUTER_API_KEY` unset, the CLI returned this local shortlist (excerpt):

```text
Local unranked fallback; Jev did not select these candidates.
- tool `exec_command`: Codex built-in exec_command for bounded local shell commands
- local command `git`: Inspect repository changes and history
- skill `create-plan`: Create an implementation plan grounded in repository context
- skill `python-packaging`: Build and distribute a Python package
```

Your shortlist depends on the tools and skills actually installed. An unranked fallback is not a Jev choice. `jevcompass install --dry-run` previews the two hook registrations without writing them; after installation, trust them in Codex `/hooks` and start a fresh session. With an OpenRouter key, an eligible task can ask Jev to rank safe metadata and may incur provider charges. [Evidence and current limits](#what-has-been-verified) distinguish hook delivery from measured benefit.

## How it works

- **Prompt hook:** `UserPromptSubmit` classifies an eligible prompt locally, then checks reviewed candidates discovered on the machine. Explicit Codex Desktop/CLI hook or skill setup can point to the installed `openai-docs` skill; routine prompts and unavailable skills can remain silent.
- **Subagent hook:** `SubagentStart` can suggest role-level candidates for Codex's built-in `explorer` and `worker` roles. That event does not include the subagent's task text.
- **Optional spawn advice:** `jevcompass install --spawn-advice` adds a narrowly matched `PreToolUse` hook for agent creation only (`Agent`, `spawn_agent`, or `collaborationspawn_agent`). It locally classifies readable child task text or a descriptive `task_name`, then sends only coarse metadata and reviewed candidate descriptions to Jev. It never gates a spawn, shell, Git, Helm, or network command. If neither field provides a clear category or no useful candidates exist, it stays silent. A normal reinstall preserves the opt-in; `jevcompass install --disable-spawn-advice` removes it. Review and trust the updated hook in `/hooks`, then start a fresh session. This is experimental and disabled by default; verify the child sees an advice ID before its first tool before relying on it.
- **Manual mode:** `jevcompass recommend --category CATEGORY --domain DOMAIN [--role ROLE]` requests advice using explicit metadata. Use `jevcompass recommend --help` for accepted values.
- **Optional skill pack:** `jevcompass skills install` makes two reviewed coding workflows available locally so a vanilla profile can receive a specific skill suggestion. It is separate from the hook installer and is never automatic.
- **Uncertainty:** Local fallback advice is labeled unranked. If there is no useful candidate, JevCompass can stay silent. A configured MCP server is not assumed to be callable in the active session.
- **Control:** Advice does not run tools or skills, change permissions, block commands, or replace project instructions and required checks.

For setup diagnostics, run `jevcompass doctor`; use `jevcompass --version` to identify the installed command and `jevcompass doctor --json` for structured output. The optional `--test-jev` flag sends one synthetic, billed request.

## Privacy

Prompt classification and candidate discovery happen locally. When optional Jev ranking is used, JevCompass sends an allowlisted category, domain, role, criteria, and generic descriptions of reviewed candidates to OpenRouter's Decisions API. It does **not** send the raw prompt, source code, diffs, repository paths, memory contents, or private skill descriptions in that request. OpenRouter receives the API key in the HTTPS authorization header.

Local metrics contain event/category/outcome/timing and a short advice ID; they do not record the prompt, model response, paths, or memory content. Local cache entries contain selection metadata. As with any external service, review OpenRouter's terms and data handling before enabling remote ranking.

Remote ranking is optional. Without a key or a reliable Jev response, JevCompass can use a small local shortlist when useful; otherwise it can stay silent. Network errors and timeouts do not block the Codex task.

## Compatibility

- Python 3.11+
- Linux: local runtime and CLI checks have been performed.
- macOS: targeted, but runtime behavior has not been verified.
- Windows: not a verified target.
- Codex Desktop and CLI: the default installation registers `UserPromptSubmit` and `SubagentStart`; optional agent-spawn advice adds only a narrowly matched `PreToolUse` hook. Host delivery is version-, trust-, and session-dependent. Fresh Desktop prompt delivery and broad subagent coverage remain unverified.

JevCompass does not infer Codex Plan UI mode. Check `/hooks` and start a fresh session after installation or a Codex upgrade.

## What has been verified

Evidence is deliberately limited to the environments tested:

- A fresh isolated Codex CLI 0.155.1 smoke using the published v0.1.12 wheel received `openai-docs` advice before its first tool, with a matching local hook ID. Additional synthetic CLI pairs confirmed remote Jev advice IDs before the first tool. The published v0.1.13 wheel adds bounded skill use/skip conditions; these checks do not prove broad coverage or effectiveness.
- The initial four blinded synthetic CLI pairs tied on task quality. Later focused pairs had mixed results, including one baseline win and one treatment win; no repeatable speed or quality improvement has been demonstrated.
- One native Desktop `explorer` child reported an advice ID before its first tool in a correlated smoke test. Other subagent probes were inconclusive, and fresh Desktop prompt delivery has not been confirmed.
- The published v0.1.15 package is available on PyPI and was installed with `pipx` from the registry in an isolated Python 3.11 profile: the default installation registered two hooks, no `PreToolUse` gate, and `doctor` passed. It includes the local security-skill relevance filter from PR #45. This validates installation, not task outcomes.
- The v0.1.14 source includes the changes from PR #40 (respect installed skill MCP prerequisites), PR #41 (C05 equal-environment comparison tied on blind scores), and PR #42 (opt-in agent-spawn advice). The spawn check was an isolated CLI source-checkout run: the child saw an advice ID before its first tool using the descriptive task title because the host message was encoded. The v0.1.14 wheel also passed an isolated pipx install and clean synthetic CLI delivery check: the child's context contained a spawn advice ID before its first tool, though it did not repeat the ID before that tool. Desktop and macOS remain unverified, and no outcome benefit has been demonstrated. The default install remains two advisory hooks; routine commands are not gated.
- macOS runtime behavior and broad usefulness remain unverified.

These checks do not establish that recommendations improve outcomes. If you test JevCompass, please share a reproducible example of advice that helped—or a case where silence was the right result.

## Soon available

More host validation, narrower coding recommendations, and a measured 20-case comparison are tracked in the [roadmap](ROADMAP.md). These are planned work, not shipped capabilities or proven productivity gains.

## Contribute

Issues and pull requests are welcome. Helpful contributions include reproducible compatibility reports, careful documentation fixes, and synthetic tests that preserve the privacy boundary. Please do not post credentials, raw prompts, private code, or unredacted logs.

## License

JevCompass is licensed under the [Apache License 2.0](LICENSE). Copyright 2026 Toni Nowak.

## Project links

- [Open an issue](https://github.com/acidkill/JevCompass/issues)
- [Propose a change](https://github.com/acidkill/JevCompass/pulls)
- [Browse the source](https://github.com/acidkill/JevCompass)
