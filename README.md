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
jevcompass doctor
```

Start with `jevcompass doctor` to see which representative tasks have enough locally available candidates for a recommendation. Before hook installation it may exit nonzero because the hooks are not registered yet; the capacity report is still useful. Then try a manual request only when the report shows a useful choice for that task; for example, run `jevcompass recommend --category coding --domain python` when **coding_python** reports `decision candidates` or `local candidates`. A clean profile may report `low signal skip` or `silent`, in which case that request can correctly return no recommendation.

To install from source instead, clone this repository and run `pipx install .` from the checkout. For the verified release, use `pipx install jevcompass==0.1.22`; check [releases](https://github.com/acidkill/JevCompass/releases) for the latest version. Version 0.1.22 puts validated candidate IDs immediately after the advice ID. It passed 276 Python tests, hosted CI, Trusted Publishing and a fresh registry pipx setup. One isolated CLI session on the published package reported `exec_command` and `unittest` before first tool; broader host-level impact is unmeasured.

The supplemental CI-guided coding pilot exposed a measurement gap: older receipts cannot distinguish exact, equivalent or chained test commands. A corrected parser separately records the exact CI invocation and the same suite without `-v`; in a new pair JevCompass ran the exact CI command while baseline ran the equivalent suite. The advised arm was slower, so no efficiency gain is established. [Pilot evidence](PILOT.md) has the blind receipts and limits.

When a coding task already states an exact test command, the source advisor skips duplicate test-selection hints. For a Python repository whose CI clearly runs unittest, the source catalog can now offer that runner for coding tasks without a prescribed test command. This relevance change is not a measured speed gain. [Pilot evidence](PILOT.md) records the control pair.

The published advice wording remains unchanged. An experimental compact source-checkout variant shortened one synthetic context but was slower than no advisor in one quality-tied P01 pair; it is not a recommended efficiency setting. [Pilot evidence](PILOT.md) records the limits.

The recommendation is environment-dependent: it can show local advice, an unranked shortlist, or no recommendation. We are testing whether advice improves completion time or token usage versus the same Codex task without it. A single keyless CLI P08 surrogate pair tied on quality and saw a shorter treatment turn. In a separate simple P01/P03 repeat, both arms completed each task and required command; treatment with local advice was slower on P01, while treatment without advice was faster on P03. These small mixed results do not prove an efficiency gain. [Pilot evidence](PILOT.md) separates observed process time, root-turn token counters, and human-rated first productive action; no billing savings are claimed. The manual command uses explicit category/domain metadata and never takes a task prompt.

To enable automatic advice, first inspect what would change, then install:

```bash
jevcompass install --dry-run
jevcompass install
jevcompass doctor
```

**What changes:** `jevcompass install` backs up an existing Codex `hooks.json` and registers two advisory hooks. Review and trust the registrations in Codex's `/hooks` screen, then start a fresh session. The default config is under `~/.codex`; set `CODEX_HOME` to use a different Codex home. A successful `doctor` check confirms local registration, not that Codex loaded or delivered advice. Recent hook metrics are matched to this Codex profile using a short local hash; records without a profile marker do not count as evidence for a fresh profile.

## Optional skills for coding and planning

**Experimental and explicitly opt-in:** versions before 0.1.16 do not include this command. If `doctor` reports `coding_python: low signal skip`, you can preview the optional bundled skills before deciding whether to install them:

```bash
jevcompass skills install --dry-run
```

The dry run does not install anything, and installing skills does not guarantee a recommendation. Install only if you choose to:

```bash
jevcompass skills install
jevcompass doctor
```

The source checkout's installer copies four skills: `jevcompass-focused-tests`, `jevcompass-regression-review`, `jevcompass-plan-implementation`, and `jevcompass-plan-cutover`. The published v0.1.22 package contains the original two coding skills; check `jevcompass skills install --dry-run` for the version you installed. The planning skills cover an ordinary implementation breakdown and a migration or cutover with rollback, respectively; they are distinct optional candidates, and Jev can compare them only when both are installed. With the default profile it uses `~/.agents/skills`; with `CODEX_HOME` set it uses that profile's `skills/` directory. It never changes hooks, executes scripts, calls Jev, or overwrites a different skill with the same name. Identical repeats are no-ops. Read the bundled `SKILL.md` files before enabling them, and start a fresh Codex session if they do not appear. Remove the installed skill directories yourself after checking their contents if you no longer want them.

Suggestions for these skills are conditional on the task matching their guidance in `SKILL.md`; a suggestion is not a requirement. This remains an experiment with no demonstrated speed or quality benefit. An installed v0.1.22 CLI surrogate for project setup delivered local `exec_command`/`git` advice before the first tool, but tied on blind scaffold quality. A subsequent paired CLI repeat retained a recognized successful unittest in both arms; it does not verify the planned Desktop case or show a performance benefit. See [pilot evidence](PILOT.md). A separate API-contract skill prototype was withheld after a opt-in blinded P07 trial in which the baseline authored the requested contract and the advised arm did not; see [pilot evidence](PILOT.md). It is not part of the installer. In one source-built C05 pair, the agent read the suggested `create-plan` skill, but the blinded scores tied 6/7. This single result does not establish causality or effectiveness. That C05 pair did not test Desktop. Separate named-role Desktop worker smokes confirmed advice delivery and one actual focused-skill read after opt-in installation, without measuring a speed or quality gain. The skills and installer are included since v0.1.16 but are not installed unless you opt in. They are absent from v0.1.15.

## Coding strategy before work (source candidate)

For a substantial coding task, use only coarse signals you verified locally. The source checkout exposes `jevcompass strategy choose --kind coding --signal existing_symbol --signal behavior_change --json`. It returns up to two reviewed strategies and labels whether Jev selected the first (`remote-choice`) or a deterministic local order applied (`no-remote-choice`). It accepts no prompt, path, source, or log text. With one clear strategy, unavailable Jev, or uncertain output, it avoids a remote choice. This explicit command is not in published v0.1.22, and no paired task gain has been measured.

## Post-change test order (source candidate)

The source checkout has an explicit `jevcompass tests rank` command for a coding agent that already knows the changed surface, plausible focused checks, and the repository-required gate. It **prints an order; it does not execute tests**. This command is not in the published v0.1.22 package, and no speed or quality improvement has been established.

```json
{"surface":"api","candidates":[{"id":"unit","kind":"unit","command":"python -m unittest tests.test_api_unit","relevance":0.5},{"id":"contract","kind":"contract","command":"python -m unittest tests.test_api_contract","relevance":0.5}],"required":[{"id":"ci","command":"python -m unittest discover -s tests -v"}]}
```

Save this as `test-order.json`, then run `jevcompass tests rank --input test-order.json --json`. `status: remote-choice` means Jev ranked genuine competing test kinds; `no-remote-choice` uses stable local relevance order when one choice is obvious, Jev is unavailable, or its answer is uncertain. The required list is returned unchanged and must still be run. Commands and caller IDs stay local: only coarse surface, test kinds, generic descriptors and opaque IDs may reach OpenRouter. Do not put secrets in command strings; this local file and CLI output are readable on your machine. Candidate discovery from changed files and matched outcome trials are still planned.

## Triage an ambiguous failed test (source candidate)

After a real test fails and you have at least two evidence-backed explanations, classify them locally and ask for a first diagnostic step. For example: `jevcompass triage --exit-code 1 --kind import --hypothesis import_module_missing --hypothesis import_path_changed --json`. The command accepts enums only; never pass a log, source snippet, path, or exception text. It prints at most two locally authored steps and the **observed failing exit code**. It does not rerun tests, execute a fix, or turn failure into success. Without an ambiguous choice or confident Jev response it uses local order. This source candidate is not in PyPI v0.1.22 and has no measured task benefit yet.

## Add your own installed skill

The built-in catalog cannot know when a private skill fits your work. Register a short, generic description explicitly, after reading its `SKILL.md` and checking the fields you are willing to share:

```bash
jevcompass skills add my-review-skill \
  --capability 'Review local code changes' \
  --use-when 'The task calls for source review' \
  --avoid-when 'The task only needs implementation' \
  --category review --domain software
```

The first run previews the fields and makes **no change**. If those fields are safe to send to OpenRouter when optional remote ranking is enabled, repeat with `--approve-remote-metadata`. Registration writes only your descriptions to `CODEX_HOME/jevcompass/catalog.json` (normally `~/.codex/jevcompass/catalog.json`); it does not copy the skill body or path. Only an actually installed skill with matching name becomes a candidate. Keep descriptions generic: the skill ID, capability, use and avoid conditions may be sent to OpenRouter for ranking. Without an OpenRouter key, matching advice stays local. Remove an entry by editing that local JSON file; a changed catalog invalidates cached rankings. A suggestion never substitutes for reading the actual `SKILL.md`.

## Enable optional Jev ranking

JevCompass works without an OpenRouter key: it can offer a local, unranked shortlist or stay silent. To let Jev rank eligible candidate choices, provide an OpenRouter API key. On a system with a supported keyring, install the optional dependency and enter the key through the hidden terminal prompt:

```bash
pipx inject jevcompass 'keyring>=25'
jevcompass auth status
jevcompass auth set
jevcompass doctor
```

For a new installation, `pipx install 'jevcompass[secure-store]'` includes the keyring dependency. If no supported keyring is available, supply `OPENROUTER_API_KEY` to the Codex process through your existing secret manager. Never paste a literal key into a hook command, repository file, or shell history. The environment variable takes precedence over the keyring; `jevcompass auth status` reports the source without printing the secret. `jevcompass auth delete` removes only the keyring entry after interactive confirmation.

`jevcompass doctor` checks model metadata without a paid decision request. Run `jevcompass doctor --test-jev` only when you explicitly want one synthetic, billed API test. A configured key permits remote ranking for eligible tasks; it does not guarantee a recommendation or prove that a hook delivered one to Codex.

## Verify advice in a fresh session

After `jevcompass install`, inspect the two registrations in Codex `/hooks`, trust them if prompted, and start a **new** Desktop or CLI session. For a focused check, give Codex a substantive task such as planning a small Python package and ask it to report any `JevCompass advice ID` and suggested IDs from its initial context **before its first tool call**. An absent ID means that session did not receive advice; `doctor` and local hook metrics alone cannot prove delivery. The advisor may intentionally stay silent when only a generic shell command is available.

If your Codex host does not load either hook, run `jevcompass doctor --json` to distinguish registration from recent invocation, and use the explicit `jevcompass recommend --category project-setup --domain python` command while investigating. Never treat a manual result as proof that a hook delivered context to an agent.

## 60-second demo

A clean Codex profile may have only a generic shell tool. JevCompass stays silent in that case because repeating “use the shell” would add no value. To see a concrete suggestion, explicitly preview and install the two optional first-party coding skills:

```bash
jevcompass skills install --dry-run
jevcompass skills install
jevcompass recommend --category coding --domain python
```

On a fresh isolated Linux profile using the published 0.1.16 package with no OpenRouter key, the final command returned this excerpt:

```text
Local unranked fallback; Jev did not select these candidates.
- tool `exec_command`: Codex built-in exec_command for bounded local shell commands
- skill `jevcompass-focused-tests`: Choose focused tests after a change and complete required repository validation
```

The skill is available locally after installation; read its `SKILL.md` and use it only when its condition fits. The recommendation does not prove the agent read or followed it, and installing a skill does not guarantee faster or better work. If you want no added skills, leave the profile unchanged; `doctor` and `recommend` will explain or demonstrate when JevCompass has enough of your existing candidates to advise. Hook installation is separate: `jevcompass install --dry-run`, then `jevcompass install`, review `/hooks`, and start a fresh session. Optional OpenRouter ranking uses coarse metadata only and may incur charges.

## How it works

- **Prompt hook:** `UserPromptSubmit` classifies an eligible prompt locally, then checks reviewed candidates discovered on the machine. Explicit Codex Desktop/CLI hook or skill setup can point to the installed `openai-docs` skill; routine prompts and unavailable skills can remain silent.
- **Subagent hook:** `SubagentStart` can suggest role-level candidates for Codex's built-in `explorer` and `worker` roles. That event does not include the subagent's task text.
- **Optional spawn advice:** `jevcompass install --spawn-advice` adds a narrowly matched `PreToolUse` hook for agent creation only (`Agent`, `spawn_agent`, or `collaborationspawn_agent`). It locally classifies readable child task text or a descriptive `task_name`, then sends only coarse metadata and reviewed candidate descriptions to Jev. It never gates a spawn, shell, Git, Helm, or network command. If neither field provides a clear category or the only candidates are generic shell and Git, it stays silent. A normal reinstall preserves the opt-in; `jevcompass install --disable-spawn-advice` removes it. Review and trust the updated hook in `/hooks`, then start a fresh session. This is experimental and disabled by default; verify the child sees an advice ID before its first tool before relying on it.
- **Manual mode:** `jevcompass recommend --category CATEGORY --domain DOMAIN [--role ROLE]` requests advice using explicit metadata. Use `jevcompass recommend --help` for accepted values.
- **Optional skill pack:** from v0.1.16, `jevcompass skills install` is an explicit, experimental opt-in that makes two reviewed coding workflows available locally. Matching-skill guidance is conditional on the task; installation is separate from hooks and never automatic.
- **Python test runner:** for `testing/python`, a clear unittest command in local GitHub Actions workflows suppresses a globally installed `pytest` candidate and offers `unittest` instead. If CI uses pytest or both runners, JevCompass does not infer a unittest requirement. Always follow the repository's exact test command.
- **Uncertainty:** Local fallback advice is labeled unranked. If there is no useful candidate, JevCompass can stay silent. A configured MCP server is not assumed to be callable in the active session.
- **Control:** Advice does not run tools or skills, change permissions, block commands, or replace project instructions and required checks.

For setup diagnostics, run `jevcompass doctor`; use `jevcompass --version` to identify the installed command and `jevcompass doctor --json` for structured output. The optional `--test-jev` flag sends one synthetic, billed request.

## Privacy

Prompt classification and candidate discovery happen locally. When optional Jev ranking is used, JevCompass sends an allowlisted category, domain, role, an optional coarse planning focus (`migration` or `implementation`), criteria, and generic descriptions of reviewed candidates to OpenRouter's Decisions API. It does **not** send the raw prompt, source code, diffs, repository paths, memory contents, or unapproved `SKILL.md` descriptions. A skill added with `--approve-remote-metadata` is an exception you control: its ID and your generic capability, use and avoid descriptions can be sent to OpenRouter when remote ranking is enabled. Preview these fields first; omit names or details you consider private. OpenRouter receives the API key in the HTTPS authorization header.

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

- The published 0.1.22 package places selected IDs at the top of each advisory. A fresh isolated CLI 0.155.1 session reported `exec_command` and `unittest` with correlated advice ID `0623c35b` before its first tool. The matching local hook took 5.99 ms, exit was 0, and the fictional fixture was unchanged. This single smoke does not measure task benefit.

- [PyPI v0.1.21](https://pypi.org/project/jevcompass/0.1.21/) and its [GitHub release](https://github.com/acidkill/JevCompass/releases/tag/v0.1.21) passed 275 Python 3.11 tests, hosted CI, Trusted Publishing, exact-tag distribution checks and a clean pipx registry install with two advisory hooks and doctor PASS. A single isolated synthetic `testing/python` request composed the shell executor and `unittest` locally in 2.96 ms; it is not a host delivery or broad effectiveness measurement.

- A fresh isolated Codex CLI 0.155.1 smoke using the published v0.1.12 wheel received `openai-docs` advice before its first tool, with a matching local hook ID. Additional synthetic CLI pairs confirmed remote Jev advice IDs before the first tool. The published v0.1.13 wheel adds bounded skill use/skip conditions; these checks do not prove broad coverage or effectiveness.
- The initial four blinded synthetic CLI pairs tied on task quality. Later focused pairs had mixed results, including one baseline win and one treatment win; no repeatable speed or quality improvement has been demonstrated.
- A native Desktop `explorer` child reported a correlated advice ID in an earlier smoke. With published 0.1.18 and two optional bundled skills explicitly installed, a fresh `worker` reported advice ID `96b34a1d` and `exec_command`/`jevcompass-focused-tests` before its first shell read; the matching local SubagentStart metric took 56.79 ms. A fresh `explorer` with only generic candidates correctly stayed silent. These are isolated role-level delivery checks; fresh Desktop prompt delivery and outcome benefit remain unverified.
- [PyPI v0.1.20](https://pypi.org/project/jevcompass/0.1.20/) and the [GitHub release](https://github.com/acidkill/JevCompass/releases/tag/v0.1.20) suppress low-signal package-docs advice when only the generic shell is available. Hosted CI and Trusted Publishing passed; a fresh Python 3.11 pipx registry install registered two advisory hooks, passed doctor, and stayed silent for package-docs in a clean profile. [PyPI v0.1.19](https://pypi.org/project/jevcompass/0.1.19/) and its [GitHub release](https://github.com/acidkill/JevCompass/releases/tag/v0.1.19) include the local Python test runner relevance fix. Hosted CI and Trusted Publishing passed; a clean registry pipx install on Python 3.11 registered two hooks, passed doctor and suggested `unittest` without `pytest` in this repository. The first `uv` install attempt briefly missed the just-published version; retry without its cache succeeded. [PyPI v0.1.18](https://pypi.org/project/jevcompass/0.1.18/) and its [GitHub release](https://github.com/acidkill/JevCompass/releases/tag/v0.1.18) contain the corrected approved-metadata disclosure. Trusted publish run 36142929270 passed; a clean PyPI pipx install on Python 3.11 registered the two default hooks and `doctor` passed. [PyPI v0.1.17](https://pypi.org/project/jevcompass/0.1.17/) and its [GitHub release](https://github.com/acidkill/JevCompass/releases/tag/v0.1.17) include the opt-in custom-skill catalog. Its PyPI README had an imprecise privacy sentence; v0.1.18 corrects it. The 0.1.17 wheel passed local content checks, 271 Python 3.11 tests, green hosted CI and isolated pipx installation. Earlier [PyPI v0.1.16](https://pypi.org/project/jevcompass/0.1.16/) and its [GitHub release](https://github.com/acidkill/JevCompass/releases/tag/v0.1.16) contain byte-identical Python modules, catalog and optional skill files. The exact-tag archive passed 254 Python 3.11 tests and an isolated pipx wheel install with two default hooks, no `PreToolUse` gate and `doctor` PASS. This validates packaging and local setup, not task outcomes or Desktop/macOS runtime.
- The v0.1.14 source includes the changes from PR #40 (respect installed skill MCP prerequisites), PR #41 (C05 equal-environment comparison tied on blind scores), and PR #42 (opt-in agent-spawn advice). The spawn check was an isolated CLI source-checkout run: the child saw an advice ID before its first tool using the descriptive task title because the host message was encoded. The v0.1.14 wheel also passed an isolated pipx install and clean synthetic CLI delivery check: the child's context contained a spawn advice ID before its first tool, though it did not repeat the ID before that tool. At the v0.1.14 source check, Desktop and macOS were unverified; later named-role Desktop smokes are described above. Fresh Desktop prompt delivery, macOS runtime and outcome benefit remain unverified. The default install remains two advisory hooks; routine commands are not gated.
- A fresh isolated Codex CLI 0.155.1 session with installed 0.1.19 reported `exec_command` and `unittest` with correlated advice ID `215744b8` before its first tool (`UserPromptSubmit/testing/local`, 33.66 ms). The fictional fixture's required unittest suite failed an existing assertion; this is local advice delivery, not remote ranking or measured benefit.
- A 0.1.19 installed-package P01 coding pair with equal opt-in skills delivered local advice before first tool, but neither arm recorded a required unittest exit. A quality correction after unblinding cannot support a blind outcome claim. See [PILOT.md](PILOT.md).
- One repeated 0.1.19 P01 pair froze an anonymous quality assessment before mapping: both helpers passed. Treatment had correlated local advice and a recognized successful unittest; baseline test execution was unobserved. This limited result does not prove that advice caused the difference or improve overall outcomes.
- A separate installed 0.1.19 P05 documentation pair tied on blind README quality. Treatment suggested only `exec_command`; a required fixture unittest failure was observed there, while baseline validation remained unobserved. Productive-action timing and advice benefit were not established.
- A fresh installed 0.1.20 Codex CLI 0.155.1 P05 smoke reported no advisory before its first tool; the matching `package-docs/low-signal-skip` hook metric took 5.73 ms, and the fictional CLI help check exited 0. This validates abstention, not a speed or quality benefit.
- The maintainer upgraded pipx from 0.1.20 to 0.1.22 without changing `hooks.json`; `doctor` still found two advisory hooks and no Jev PreToolUse gate. A native Desktop `worker` reported ID `9ceb0fdd` and `exec_command`/`jevcompass-focused-tests` before its first tool, matching a `SubagentStart/coding/local` metric of 54.64 ms. This is one role delivery smoke, not fresh Desktop prompt delivery or skill-use evidence.
- A corrected P07 CLI pair on published 0.1.22 used a writable fictional API-contract fixture. Blind authored-file review favored baseline; treatment returned `insufficient-candidates` and no Jev advice. This pair measures ordinary model variation under abstention, not an effect of JevCompass. See [pilot evidence](PILOT.md).
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
