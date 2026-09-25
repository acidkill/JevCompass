# JevCompass

**Choose the right Codex tools and skills for the task, with local discovery and optional Jev ranking.**

JevCompass adds concise, non-blocking advice to Codex Desktop and CLI. It checks a reviewed catalog against your local Codex configuration, installed skills, and available commands. When several useful choices remain, it can ask Jev through OpenRouter to rank their generic descriptions. JevCompass does not send raw prompts or project files to OpenRouter.

> The GitHub repository and its current release assets are private. Installation from the checkout or a release wheel requires access. Public PyPI installation is not available until a separately verified release.

## Get started

**Invited users:** accept the GitHub repository invitation while signed into the invited account, then clone and enter the project:

```bash
git clone https://github.com/acidkill/JevCompass.git
cd JevCompass
```

Run the commands below from that checkout. If you were sent a release wheel instead, download it from the private release using that same account and verify the file before installation. The advice example below is illustrative; verify your first result with `jevcompass recommend --category project-setup --domain software`, then open a fresh Codex session for hook delivery.

Requirements: Python 3.11+, `pipx`, and a Codex Desktop or CLI version supporting `UserPromptSubmit` and `SubagentStart` hooks. JevCompass targets macOS and Linux; Linux has local runtime validation, while macOS runtime validation is pending. You can start without an OpenRouter key, additional skills, or MCP servers.

From a checkout you can access:

```bash
pipx install .
jevcompass install --dry-run
jevcompass install
jevcompass doctor
```

Or install the [private v0.1.10 wheel](https://github.com/acidkill/JevCompass/releases/tag/v0.1.10) after downloading and verifying it:

```bash
pipx install ./jevcompass-0.1.10-py3-none-any.whl
jevcompass install
jevcompass doctor
```

`install` backs up the active `hooks.json` before changing it, merges exactly two JevCompass hook registrations, and preserves unrelated hooks such as smem. It removes only recognized legacy Jev `PreToolUse` gates. The default config path is `~/.codex/hooks.json`; `CODEX_HOME` changes it. Repeating `install` is safe. No Node runtime is required.

**Finish setup in Codex:** open `/hooks`, review and trust the new `UserPromptSubmit` and `SubagentStart` entries, then start a fresh session. A registration shown by `doctor` does not prove that the host loaded or trusted it. Run `jevcompass doctor --json` for structured diagnostics. For a new wheel installed through pipx, reinstall the wheel, reinject optional extras if used, check `doctor`, and review hook trust again. A verified replacement on the maintainer's Linux pipx profile required uninstalling before reinstalling; `pipx install --force` did not work against that uv-backed environment.

Once a PyPI release has been independently verified, the registry installation command will be `pipx install jevcompass`. Do not assume the pending Trusted Publisher makes this command available today.

## What you get

| Entry point | When it helps | What happens |
| --- | --- | --- |
| `UserPromptSubmit` | Substantive tasks in ordinary Codex sessions | Classifies a category locally; emits brief optional advice when the reviewed catalog has useful candidates. Short requests can be skipped. It does not infer the Plan UI mode. |
| `SubagentStart` | Built-in `explorer` and `worker` roles | Suggests role-level candidates only when the signal is useful; generic and custom roles are skipped. The event has no subagent task text. |
| `jevcompass recommend` | Short or ambiguous tasks, or manual use | Takes an explicit category, domain, and optional role; never accepts the raw prompt. |
| `jevcompass doctor` | Setup and troubleshooting | Reports registration, local catalog capacity, model/key status and the latest redacted invocation status separately for each hook. |

The hook adds **context, not control**. It never blocks shell, Git, Helm, or network commands, grants permissions, installs skills, or replaces required project instructions and tests. A locally configured MCP entry does not prove the tool is connected in the active session. Codex must confirm each recommendation is usable and read the selected `SKILL.md`.

Example advice from a repository setup task in a profile with the `create-plan` skill (the ID is illustrative):

```text
JevCompass advice ID: 0123abcd
Local unranked fallback; Jev did not select these candidates. Optional tools and skills for this task; validate against the task and actual availability:
- tool `exec_command`: Codex built-in exec_command for bounded local shell commands
- local command `git`: Inspect repository changes and history (run through `exec_command`; confirm it is available in this session)
- skill `create-plan`: Create an implementation plan grounded in repository context
```

Without a key or a reliable Jev result, a small local shortlist can still appear; it is labeled **unranked**. One specific candidate is suggested locally. A sole generic `exec_command` candidate is suppressed as low signal. Silence is a valid result, especially in a clean Codex profile. `git` and `pytest` are commands through `exec_command`, not separate model-facing tools. Some advice includes extra locally composed reminders about skill use, required checks, and plan execution roles.

## Optional Jev ranking

Set `OPENROUTER_API_KEY` in the hook process environment to allow remote Decisions requests. The default model is `typesafe/jev-1.13`; set `JEVCOMPASS_MODEL` to override it with a compatible Decisions model. GUI-launched Desktop sessions may not inherit a terminal environment. The optional secure-store extra supports native macOS Keychain and Linux Secret Service/KWallet:

```bash
pipx inject jevcompass 'keyring>=25'
jevcompass auth set
jevcompass auth status
```

For a checkout, `pipx install '.[secure-store]'` installs the extra together with JevCompass. `auth set` reads a hidden interactive input rather than taking a key on the command line. The environment takes precedence over the keyring. A locked, unavailable, unsupported, or slow keyring falls back to local advice. Native keyring access can ask for OS authorization; configure it interactively before relying on it in an unattended hook. On macOS, Keychain may authorize the pipx Python executable; review its access controls. `auth status` and `doctor` redact the credential. For keyring troubleshooting, run `env -u OPENROUTER_API_KEY jevcompass auth status` to check the store without the terminal environment masking it.

`doctor` checks public model metadata without a paid decision. `jevcompass doctor --test-jev` explicitly sends one synthetic, billed request. Network errors, model uncertainty, and timeout cause the hook to omit remote ranking rather than block the user.

## Try explicit advice

```bash
jevcompass recommend --category project-setup --domain software
jevcompass recommend --category debugging --domain python --role primary
jevcompass recommend --category documentation --domain codex
```

Categories: `infrastructure`, `debugging`, `testing`, `research`, `api-design`, `documentation`, `package-docs`, `coding`, `codebase`, `history`, `review`, `source-review`, `planning`, `operations`, `project-setup`. Domains: `general`, `software`, `python`, `web`, `shell`, `kubernetes`, `codex`. Roles: `primary`, `planner`, `explorer`, `worker`. Use `--help` for the current CLI contract. `package-docs` is for Python package installation documentation; `codex` domain is for substantive Codex documentation or troubleshooting. The command accepts no prompt, paths, or code.

## Privacy and diagnostics

Task classification and catalog discovery happen locally. A Decisions request contains an allowlisted category, domain, role, criteria, and generic descriptions of a small set of reviewed candidates. It does **not** include the prompt, source code, diffs, repository paths, memory contents, local integration names, or private skill descriptions. OpenRouter receives the API key in the HTTPS authorization header when a remote request occurs. Review its service terms and data handling for your use case. A configured MCP entry is not automatically recommended merely because it appears in `config.toml`.

The local cache and metrics live under `~/.cache/jevcompass` and `~/.local/state/jevcompass`. Cache entries contain allowlisted selection metadata; metrics record safe event/category/outcome/timing details and a short advice ID. Simple skipped prompts need not create a metric. `doctor` reports the most recent safe status separately for each hook found in the bounded metric tail; `not observed in metric tail` is inconclusive, while `low-signal-skip` means the adapter ran and intentionally withheld advice. The displayed log age belongs to the file's most recent write, not to each hook event. A metric proves the adapter ran, **not** that the agent saw advice. From a source checkout, `python scripts/probe_hook_delivery.py --event prompt` or `--event subagent` runs an isolated synthetic CLI canary with a temporary profile and workspace. Its summary exposes status and event ordering only. A probe with no observed hook, child, or spawn event is inconclusive; it does not diagnose production hook delivery. To confirm real delivery, ask the agent at the beginning of a fresh session whether a JevCompass advice ID appeared before its first tool and correlate it with the local metric. `JEV_ADVISOR_DIAGNOSTIC=1` temporarily adds sanitized event-mode diagnostics. Never paste private prompts or credentials into issue reports.

### If you see no advice

1. Run `jevcompass doctor` and `jevcompass install --dry-run`; inspect the active Codex config source.
2. Check `/hooks` for each registration, active state, and trust review. Reopen a fresh Codex session after changing the definition.
3. Try `jevcompass recommend --category project-setup --domain software`. This tests manual selection but does **not** prove host hook delivery.
4. A short request, unknown category, generic subagent role, missing reviewed candidates, or a sole shell candidate can correctly yield no recommendation. A missing key yields local guidance when useful.
5. For remote ranking, check `jevcompass auth status`, model metadata in `doctor`, and use `doctor --test-jev` only when a billed synthetic request is acceptable.

Report failures with the Codex host/version, OS, Python version, category, redacted doctor output and safe metric status. Keep secrets, raw prompts, private paths, and proprietary logs out of reports.

## Current evidence and limits

Local Linux unit, packaging, and isolated pipx checks are recorded for private releases in `TASKS.md`; the current private GitHub release is v0.1.10. Four matched synthetic CLI task pairs (P01, P03, P05, P07) tied in blind quality ratings; this does not establish a speed or quality improvement. Doctor's per-hook metrics show adapter invocation status, not agent-visible advice. The isolated prompt/subagent diagnostic probes test synthetic canaries; recent subagent probe runs did not observe a child or delivery, so they do not establish SubagentStart delivery or failure. Fresh Desktop **prompt** delivery, macOS runtime behavior, broad usefulness, and active-session MCP availability remain unverified. The stronger 20-pair study is planned in [ROADMAP.md](ROADMAP.md); detailed observations live in [PILOT.md](PILOT.md).

The GitHub repository is private and has no open-source license. The reported PyPI Trusted Publisher is pending; no production registry artifact has been verified. GitHub CI run [36087868425](https://github.com/acidkill/JevCompass/actions/runs/36087868425) failed before runner steps; each job has an empty step list, with GitHub's payment-or-spending-limit annotation. The account-side cause is unverified. The package is available today only to collaborators with repository or wheel access. [Release and adoption tasks](TASKS.md) distinguish completed work from open checks.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
PYTHONPATH=src python -m unittest discover -s tests -v
python -m build
python tools/check_distribution.py
```

The unit suite covers hook output, classification, minimization, uncertainty, cache, catalog availability and installer merge. Synthetic tests do not prove host delivery or benefit. See [CONTRIBUTING.md](CONTRIBUTING.md) for local development and safe reports.

## Soon available:

- Wider Desktop and CLI validation with blinded usefulness and speed measurements.
- Task-specific test ordering, error triage, and priority advice if matched trials show benefit.
- Registry installation after the independent publication and runner checks. See [ROADMAP.md](ROADMAP.md).
