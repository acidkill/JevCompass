# JevCompass v0.1.12

This release adds focused setup advice for users configuring Codex Desktop and CLI. Explicit hook, settings, and skill setup requests can suggest the installed official `openai-docs` skill. The advisor excludes general office-document tooling from that category and stays silent when the skill is unavailable. Everyday shell and Git commands remain outside JevCompass's hooks.

A synthetic Codex CLI 0.155.1 session confirmed that this advice reached the agent before its first tool; the corresponding local hook took 2.75 ms. A current Desktop `explorer` negative control showed that the hook ran and intentionally stayed silent when only the generic shell was available. These are delivery and abstention checks, not measured speed or quality improvements. See [PILOT.md](PILOT.md) for the evidence and limits.

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
