# JevCompass v0.1.19

This release avoids offering a globally installed `pytest` as the Python testing tool when a repository's local GitHub Actions commands unambiguously require `python -m unittest`. In that case the catalog offers a generic `unittest` candidate and still asks the agent to inspect the exact repository command. A pytest CI command remains eligible; mixed runner signals leave both as project decisions rather than assuming unittest. CI workflow content stays local; only generic candidate metadata reaches optional Jev ranking. The trigger was a published 0.1.18 remote choice for `testing/python` that selected pytest in this unittest-gated repository. This change improves candidate relevance, not a measured speed or quality gain. PR #87 passed hosted CI at a144463 and 272 local Python 3.11 tests. Annotated v0.1.19 peels to 3f74c74; exact-tree wheel/sdist audit and [PyPI Trusted Publishing run 36145304388](https://github.com/acidkill/JevCompass/actions/runs/36145304388) passed. A clean registry pipx installation initially hit a stale uv index cache, then passed with `UV_NO_CACHE=1`; two-hook setup, doctor and local unittest/no-pytest recommendation passed. The maintainer's pipx upgrade preserved the production hooks.json hash.

Install with `pipx install jevcompass==0.1.19`, then inspect `jevcompass install --dry-run`, run `jevcompass install` and `jevcompass doctor`. Required project tests remain authoritative. Fresh Desktop prompt delivery, macOS runtime, and repeatable efficacy remain unverified.

---

# JevCompass v0.1.18

This patch corrects the privacy explanation for the optional custom-skill catalog. The user's approved skill ID and generic capability/use/avoid fields may be sent to OpenRouter when remote Jev ranking is enabled; raw `SKILL.md` contents, paths, prompts and code remain local. The CLI preview and `--approve-remote-metadata` behavior are unchanged. Install with `pipx install jevcompass==0.1.18`. This is a documentation correction, not a new effectiveness claim. README, ROADMAP, TASKS and RELEASE_NOTES were aligned before the final tag; CI now requires all four documents to change in a PR that bumps the package version. PR #83 passed hosted CI at 99b1790; final annotated tag v0.1.18 peels to d4d2969. Exact-tree wheel/sdist audit and isolated pipx wheel setup passed. [PyPI Trusted Publishing run 36142929270](https://github.com/acidkill/JevCompass/actions/runs/36142929270) succeeded; [PyPI v0.1.18](https://pypi.org/project/jevcompass/0.1.18/) shows the corrected description. A clean Python 3.11 pipx registry install, default two-hook setup and `doctor --json` passed. A later maintainer-profile native Desktop `worker` on installed 0.1.18 reported advice ID `96b34a1d` and the optional focused-tests skill before its first shell read, matching a 56.79 ms local hook metric. This is role-context delivery after explicit skill installation. macOS runtime, fresh Desktop prompt delivery, actual skill use and measured benefit remain unverified.

---

# JevCompass v0.1.17

This update makes the first useful recommendation reproducible in a fresh profile: `doctor` points to the optional two-skill pack, and the README shows a keyless local shortlist without pretending that a candidate is a measured benefit. Installed custom skills can now be added to a per-profile catalog with `jevcompass skills add`. The command previews short generic fields, requires explicit `--approve-remote-metadata` to save, and never copies the skill body or path. Only installed names become candidates; editing the local catalog changes the cache key.

The default install still has two nonblocking advisory hooks. The optional skill pack and custom-skill registration require explicit user action. Remote Jev ranking is optional and may send approved generic candidate metadata to OpenRouter. A fresh Desktop prompt, macOS runtime, and a repeatable speed or quality gain remain unverified; see [PILOT.md](PILOT.md).

Install with `pipx install jevcompass==0.1.17`, then run `jevcompass install --dry-run`, `jevcompass install`, and `jevcompass doctor`. Read [README.md](README.md) for the optional skill and key setup. The source release is gated by the Python suite, distribution audit, isolated pipx install, and green hosted CI before tagging and registry publication.

---

# JevCompass v0.1.16

This release adds conditional guidance to read a suggested skill's `SKILL.md` when its use condition matches the task (PR #63). It also includes an explicitly opt-in, experimental `jevcompass skills install` command for two bundled coding workflows. The default installation continues to register two nonblocking advisory hooks. Skills are never installed automatically, and advice does not run a skill or replace required project instructions and checks. Install with `pipx install jevcompass==0.1.16`, inspect `jevcompass install --dry-run`, run `jevcompass install` and `jevcompass doctor`, then review `/hooks` in a fresh Codex session. Only after reading the bundled skill files, opt in with `jevcompass skills install --dry-run` and `jevcompass skills install` if desired.

One source-built C05 pair read the suggested `create-plan` skill, while the blinded scores tied 6/7. This is a single limited observation and demonstrates no speed or quality benefit. Desktop behavior remains unverified. [GitHub v0.1.16](https://github.com/acidkill/JevCompass/releases/tag/v0.1.16) is public and the [PyPI Trusted Publishing run 36130543387](https://github.com/acidkill/JevCompass/actions/runs/36130543387) passed from annotated tag `v0.1.16`, which peels to `5ef0fb6ecb9ab6e8778d2c69fd4a38420d40c7f3`. The exact-tag archive passed 254 Python 3.11 tests, wheel/sdist content checks, and isolated pipx Python 3.11 wheel installation with two default hooks and `doctor` PASS. Downloaded GitHub asset SHA-256 digests are wheel `986e6871d16bd338a508646983e8fda990c60e70175ea7a8e5fc95afda` and sdist `5fc3766fa4194512a0b9479d3f23f77c448a9673b05073320bc4ed846bfc8a69`. [PyPI 0.1.16](https://pypi.org/project/jevcompass/0.1.16/) reports wheel `b9386927cd97e304997f96f0683ce434980ca9731433118a70e2bdea1954b35c` and sdist `f89cdd167d6fb5fd96d750e12645df8e08e0e6c00cddfe863157aee4ba45bedf`; all 13 packaged Python/catalog/skill files match the GitHub wheel byte for byte. After a short simple-index propagation delay, a fresh `pipx install jevcompass==0.1.16` on Python 3.11 succeeded from PyPI; the default two-hook install and `doctor --json` passed, and the experimental skill installer stayed opt-in. JevCompass remains Apache-2.0 licensed. Privacy boundaries and platform limitations are described in [README.md](README.md).

---

# JevCompass v0.1.15

This patch narrows security-specific skill suggestions: an ordinary code review no longer offers `security-requirement-extraction` solely because it matches the broad software domain. Explicit security, authentication, and signed-webhook tasks can still receive it. The signal is checked locally; task text is never sent to Jev. Manual `recommend --domain security` remains available.

Install with `pipx install jevcompass==0.1.15`, then run `jevcompass install` and `jevcompass doctor`. The default installation has two nonblocking advisory hooks. Agent-spawn advice remains opt-in. Required project instructions and tests remain authoritative. This release changes candidate relevance; it does not establish faster task completion or higher task quality. See [PILOT.md](PILOT.md) for the current evidence limits.

PR #48 passed hosted Python CI at exact head `b607924`, and annotated tag `v0.1.15` peels to merge commit `d3214509ecfa997fe36b40290a4931373ae1994b` with the same tree. The exact-tag archive passed 225 Python 3.11 tests, wheel/sdist content checks, and an isolated pipx wheel install with `doctor` PASS. The [GitHub release](https://github.com/acidkill/JevCompass/releases/tag/v0.1.15) has independently downloaded SHA-256 digests: wheel `3971d0a913f057f3b873556505f474659e36ab496f0ed0b947493c1cf3d0828b`, sdist `4d5267c7ffdfda0b6262736f67bbdaa5cd8659d738fd9a10bf616393e7769402`.

[PyPI 0.1.15](https://pypi.org/project/jevcompass/0.1.15/) was published through [Trusted Publishing run 36114841895](https://github.com/acidkill/JevCompass/actions/runs/36114841895). The separately built registry artifacts have SHA-256 wheel `e364bb3530b35ac4447a76595e388803133bcb9c7a3e540ec439879582d2941a`, sdist `7b008e4583a9b0aa9e8995b89813ae941a0d66a9b11da50b8895db3f350a3ca9`. The ten packaged Python/catalog files in the PyPI and GitHub wheels matched byte for byte despite different archive digests. A fresh pipx installation from PyPI eventually succeeded after a transient index cache miss; the default two-hook install and `doctor --json` passed. macOS runtime and measurable task benefit remain unverified.

---

# JevCompass v0.1.14

This release respects installed skill MCP prerequisites during candidate discovery (PR #40) and adds **optional** task-aware advice when Codex creates an agent (PR #42). The default installation still registers only `UserPromptSubmit` and `SubagentStart`. The extra hook is matched to agent creation only and never gates shell, Git, Helm or network commands.

Install [v0.1.14 from PyPI](https://pypi.org/project/jevcompass/0.1.14/) with an isolated pipx environment:

```bash
pipx install jevcompass==0.1.14
jevcompass install --dry-run
jevcompass install
jevcompass doctor
```

The PyPI Trusted Publisher workflow completed successfully in [GitHub Actions run 36114012972](https://github.com/acidkill/JevCompass/actions/runs/36114012972) from tag commit `7e44137f3471532dfbf74d7a61c0e8946a58062f`. PyPI JSON reports wheel SHA-256 `b4331c437f1b193ffd50a3da60dcf6105e0cb50b6f085f4745f14cd5ec1bcd7a` and sdist SHA-256 `a066bef4c3fd700b938e1b9f705556d755a4d423e33a92a5653c4b6f22e27303`. An isolated pipx install from the published registry package succeeded; the default install's doctor check passed with two hooks and no `PreToolUse` hook.

To try the experimental spawn advisor, run `jevcompass install --spawn-advice`, review and trust the new entry in Codex `/hooks`, and start a fresh session. `jevcompass install --disable-spawn-advice` removes only that entry. The installer backs up an existing `hooks.json` when it changes it and preserves unrelated smem hooks.

One isolated CLI source-checkout experiment confirmed that the child saw an advice ID before its first tool when the host encoded the message and supplied a descriptive task title. It has not established skill use, improved outcomes, fresh Desktop delivery or macOS compatibility. The C05 equal-environment planning pair (PR #41) tied 6/7 on blind scores and showed no speed gain. These results do not establish measured task benefit. JevCompass remains [Apache-2.0 licensed](LICENSE). Previous release: [v0.1.13](https://github.com/acidkill/JevCompass/releases/tag/v0.1.13).

The annotated tag peels to `7e44137f3471532dfbf74d7a61c0e8946a58062f`. PR #43 passed hosted CI; an exact-source archive passed 224 Python 3.11 tests and the wheel/sdist content audit. An isolated pipx Python 3.11 install confirmed the default pair, reversible spawn opt-in and `doctor` PASS. A clean synthetic CLI child received the wheel's spawn advice in context before its first tool; it did not echo the ID before that tool. Public assets were downloaded independently and matched SHA-256: wheel `edd405e752544476ac67efc112eb45e8cb47f9eafab93f715d1b1fc3e9a53c33`, sdist `8d206b3530c08531eff73557d9c02f26d22b08b3fd83c161a9813a1b36b6bee0`. This is delivery evidence, not measured task benefit.

---

# JevCompass v0.1.13

Skill advice now includes concise catalog conditions for when to use or skip a suggested skill. The agent is prompted to inspect task scope before opening one, which helps avoid treating every suggestion as mandatory. If these conditions would exceed the advisory size limit, JevCompass keeps the shorter bounded advice. The two hooks remain optional and nonblocking; routine shell and Git commands stay outside them.

Synthetic Codex CLI checks have confirmed a remote Jev choice and advice ID reaching an agent before its first tool. A local build of this change ran three small review pairs without a candidate skill read before source inspection, but their source-read times were mixed. These observations do not establish a productivity or quality improvement. The full evidence and limitations are in [PILOT.md](PILOT.md).

## Install from the GitHub release

Download the v0.1.13 wheel from [GitHub releases](https://github.com/acidkill/JevCompass/releases), verify its SHA-256 against the release asset digest, then run:

```bash
pipx install ./jevcompass-0.1.13-py3-none-any.whl
jevcompass install --dry-run
jevcompass install
jevcompass doctor
```

Review and trust the two advisory hooks in Codex `/hooks`, then start a fresh session. Existing users should follow pipx replacement instructions for a locally downloaded wheel. The installer backs up `hooks.json` when it modifies it.

JevCompass is [Apache-2.0 licensed](LICENSE). macOS runtime, fresh Desktop prompt delivery, broad recommendation coverage and measured productivity gains remain unverified. PyPI publication is a separate decision. Previous release: [v0.1.12](https://github.com/acidkill/JevCompass/releases/tag/v0.1.12).

---

# JevCompass v0.1.12

This release adds focused setup advice for users configuring Codex Desktop and CLI. Explicit hook, settings, and skill setup requests can suggest the installed official `openai-docs` skill. The advisor excludes general office-document tooling from that category and stays silent when the skill is unavailable. Everyday shell and Git commands remain outside JevCompass's hooks.

A synthetic Codex CLI 0.155.1 session confirmed that this advice reached the agent before its first tool; the corresponding local hook took 2.75 ms. A current Desktop `explorer` negative control showed that the hook ran and intentionally stayed silent when only the generic shell was available. These are delivery and abstention checks, not measured speed or quality improvements. See [PILOT.md](PILOT.md) for the evidence and limits.

The annotated source tag peels to `9c78801c75fc063ef68369f16e1105125007dbb9`. Hosted PR #16 CI passed all 180 Python 3.11 tests; the exact-source archive passed the same suite and distribution-content check. Isolated pipx installation from the built wheel succeeded. Independent downloads match GitHub's SHA-256 digests: wheel `e2df11a7e83e5108f976a44c861125c35234d8dbdb853a5352ce9276d380b462`, sdist `da0292fa103158668da9b77c234655e33b684f2178afcbc0cbaa1849748df8cb`.

## Install from the GitHub release

Download the v0.1.12 wheel from the [GitHub releases page](https://github.com/acidkill/JevCompass/releases), compare its SHA-256 with GitHub's asset digest, then run:

```bash
pipx install ./jevcompass-0.1.12-py3-none-any.whl
jevcompass install --dry-run
jevcompass install
jevcompass doctor
```

Review and trust the two advisory hooks in Codex `/hooks`, then start a fresh session. Existing installations can use `pipx upgrade jevcompass` when installed from an upgradeable source; otherwise install the verified wheel with pipx following its replacement instructions. The installer backs up `hooks.json` when it modifies it.

JevCompass is [Apache-2.0 licensed](LICENSE). This GitHub release does not establish macOS runtime behavior, fresh Desktop prompt delivery, broad advice coverage, or measured productivity gains. PyPI publication is a separate step. Previous release: [v0.1.11](https://github.com/acidkill/JevCompass/releases/tag/v0.1.11).
