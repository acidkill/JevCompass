# JevCompass launch kit

This file contains draft copy and a launch checklist. Nothing here is an announcement that has been posted or a claim that PyPI is live.

## Positioning

**Tagline:** Find the right Codex tool or skill before the work begins.

**One-sentence pitch:** JevCompass gives Codex Desktop and CLI a short, optional recommendation from a locally checked tool and skill catalog at the start of a substantive task or supported subagent session.

**Who it is for:** Developers using Codex who have several tools, skills, or integrations and want a quick first choice without adding a gate to everyday commands.

**What makes it different**

- Two advisory hooks: `UserPromptSubmit` and `SubagentStart`. No Jev `PreToolUse` gate.
- Local classification and availability checks; only approved, coarse candidate metadata can reach the optional OpenRouter Decisions API.
- A local shortlist when remote selection is unavailable. The advisor stays quiet when only a generic shell suggestion remains.
- `jevcompass doctor` explains the installed hooks and local candidate capacity. `jevcompass recommend` accepts an explicit category and domain.

These are product behaviors, not measured productivity gains. Four synthetic CLI task pairs tied on quality; broader efficacy and current Desktop prompt delivery remain to be measured.

## GitHub About

**Description:** Tool and skill suggestions for Codex Desktop and CLI, with local-first privacy and optional Jev ranking.

**Topics:** `codex`, `codex-cli`, `codex-desktop`, `ai-agents`, `agent-skills`, `tool-selection`, `mcp`, `openrouter`, `python`, `developer-tools`, `jev`.

A private repository and its content are visible only to authorized collaborators. Topics are public metadata. Do not add a public homepage or claim public installation until the package can actually be installed.

## Announcement drafts

### Invited access (usable while the repository is private)

> Introducing JevCompass: optional tool and skill advice for Codex Desktop and CLI. It checks a curated local catalog when a substantive task begins, and stays silent for routine requests. The two hooks advise; they do not block commands. Invited users can install the verified wheel with pipx, run `jevcompass install`, then confirm the setup with `jevcompass doctor`. If you try it, tell us whether the advice reached the agent before its first tool and whether it helped you reach the first useful action.

Share the private download and exact digest only with collaborators who have repository access. Avoid posting a private-release URL as if everyone can open it.

### Public registry (hold until a verified PyPI upload)

> JevCompass helps Codex Desktop and CLI pick from the tools and skills actually available in your setup. Install with `pipx install jevcompass`, run `jevcompass install`, and check `jevcompass doctor`. The advisor uses two non-blocking hooks; OpenRouter/Jev ranking is optional, and raw prompts or code are not sent by the advisor. We would love a reproducible example where advice helped, or where it stayed silent when a suggestion would have been noise.

Publish this version only after the PyPI project and `pipx install jevcompass` are verified from a clean environment. Do not imply the repository is public unless its visibility has changed.

### Short social copy

> A compass for Codex tools and skills. JevCompass offers a small, optional recommendation when a real task starts and gets out of the way for everyday commands. Local catalog first; Jev ranking when useful. [Insert an actually accessible install link after verification.]

## One-minute demo outline

1. Show `jevcompass doctor` in a clean profile: two advisory hooks registered, no tool gate, and the local candidate-capacity report.
2. Use a synthetic Git repository and a read-only review prompt. Before the first tool, show the agent-visible advice ID and the locally available `exec_command`/`git` suggestions.
3. Run `jevcompass recommend --category review --domain python` to show the explicit path. Explain that results depend on installed candidates.
4. Use a short routine prompt and show that JevCompass adds no recommendation.
5. Close with the privacy boundary and a link to the README. Do not show a real API key, private code, user prompt, or memory contents.

The recorded CLI 0.1.8 example is advice ID `a14d8368` with a local fallback and a 62.64 ms hook metric. A video or screenshot still needs to be captured and checked; this outline is not media evidence.

## FAQ for first users

**Does JevCompass block tools or grant permissions?** No. It adds advice to qualifying Codex prompt and supported subagent events. Codex permissions and repository instructions still apply.

**Do I need an OpenRouter key?** No. Without one, JevCompass can offer a small local shortlist when reviewed candidates exist. Jev ranking requires a configured key and sends only coarse, curated metadata.

**Why did I get no advice?** A short or routine request may be skipped, a supported role may lack task context, or the local catalog may have no specific verified candidate. Check `jevcompass doctor` and `jevcompass recommend --category review --domain python`.

**Does it install or run suggested skills?** No. Read the installed skill before use and confirm that suggested tools exist in the active session.

**Is a result guaranteed to speed up my task?** No. Early synthetic comparisons were quality ties. We are collecting agent-visible delivery and first productive-action evidence.

## Launch readiness

- [x] Private GitHub wheel/sdist and active Linux pipx installation verified at v0.1.9 (Linux scope and limits are recorded in `TASKS.md`).
- [x] One fresh CLI v0.1.8 agent-visible advice ID confirmed before its first tool.
- [ ] Fix GitHub Actions billing and run the declared Linux/macOS checks.
- [ ] Verify a fresh Desktop prompt receives advice before the agent's first tool.
- [ ] If choosing public PyPI distribution, confirm the pending publisher, run the approved workflow, inspect the public project and install it with pipx from a clean profile.
- [ ] If choosing public source access, separately review repository visibility, license, support process, and included files.
- [ ] Capture and review a synthetic demo screenshot/video before attaching it to a public announcement.

## Inspiration and attribution

The structure follows useful patterns in recently surfaced developer-tool repositories: a direct promise, a minimal first command, separate platform setup, and a link to deeper guides. These are examples from GitHub Trending and independent roundups, **not** GitHub-awarded “Repository of the Day/Week” winners: [Google AX](https://github.com/google/ax), [OpenAI Codex](https://github.com/openai/codex), [obra/superpowers](https://github.com/obra/superpowers), and [Strands Harness SDK](https://github.com/strands-agents/harness-sdk). GitHub's [topics guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics) informed the About metadata.
