# JevCompass launch kit

Public-facing positioning and copy for JevCompass. Keep every statement aligned with the current project evidence in this file and the [README](README.md).

## Positioning

**Tagline:** Find a useful Codex tool or skill before your task gets moving.

**One-sentence description:** JevCompass checks a reviewed local catalog of tools and skills, then offers optional advice when it finds a specific candidate for a Codex task.

**The problem:** Codex users can have many tools, skills, and integrations available. Finding the relevant one at the start of a task adds friction; recommending everything adds noise.

**The approach:** Discover candidates locally, suggest only when there is a useful choice, and leave execution and permissions with Codex.

**For:** Developers who use Codex Desktop or CLI and want a lightweight starting point for tool and skill discovery.

## GitHub About

**Description:** Local-first tool and skill suggestions for Codex Desktop and CLI, with optional Jev ranking.

**Suggested topics:** `codex`, `codex-cli`, `codex-desktop`, `agent-skills`, `tool-selection`, `developer-tools`, `python`, `openrouter`.

GitHub says README files should explain why a project is useful and how people can use it; topics help people find and contribute to relevant repositories. Keep the repository description, topics, and README consistent with verified support. [GitHub repository customization](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository) · [GitHub topics guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics)

## Short announcement

> Codex has plenty of tools. JevCompass helps you choose where to start. It checks a reviewed catalog against what is available locally, then can add a short, optional recommendation at the beginning of a task. Advice stays advisory: Codex keeps control of tools, permissions, and project checks. Try the source install and tell us where it helped—or where silence was better.

**Accuracy note:** Do not claim proven productivity gains, universal compatibility, a verified PyPI package, or official affiliation with OpenAI/Codex.

## 60-second demo

From a public source checkout with Python 3.11+ and pipx available:

```bash
pipx install .
jevcompass recommend --category review --domain python
jevcompass install --dry-run
```

Show that the explicit recommendation is environment-dependent, then inspect the dry-run before changing the Codex hook configuration. For the automatic path, run `jevcompass install`, review the two hook registrations in Codex `/hooks`, trust them, and begin a fresh session.

Do not script a particular recommendation as guaranteed. Do not describe `doctor` or a local metric as proof that advice reached the agent. The manual recommendation path and automatic hook delivery are different checks.

## Privacy and behavior talking points

- Prompt classification and local candidate discovery happen on the machine.
- Optional Jev ranking sends coarse allowlisted metadata and generic reviewed candidate descriptions to OpenRouter; it does not send the raw prompt or project files.
- If remote ranking is unavailable, local advice may be unranked; when the candidate signal is weak, JevCompass may stay silent.
- Hooks add context only. They do not invoke tools, install skills, change permission settings, or replace the repository's instructions and checks.
- Review OpenRouter's terms and handling before configuring remote ranking.

## Evidence and limits

- One isolated Linux Codex CLI 0.155.1 smoke received prompt advice before its first tool. This is a delivery observation for one setup, not proof of benefit.
- Four initial blinded synthetic CLI pairs tied on task quality; later focused pairs were mixed. No repeatable improvement has been demonstrated.
- One correlated native Desktop `explorer` smoke delivered advice before its first tool. Fresh Desktop prompt delivery and broad subagent coverage remain unverified.
- macOS runtime behavior, Windows support, and broad usefulness are unverified.

Point readers to the README for current install and compatibility details. Update this section only when new evidence has been checked against the actual host, version, and test setup.

## Contribution invitation

> Try the source install, then share a small, reproducible report: your Codex host and version, OS, Python version, whether advice appeared before the first tool, and whether the recommendation was useful. Please redact credentials, prompts, code, local paths, and private logs.

Issues and pull requests are welcome. Focus reports on reproducible behavior, respect the documented privacy boundary, and keep claims aligned with verified results.