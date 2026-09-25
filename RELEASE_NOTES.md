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
