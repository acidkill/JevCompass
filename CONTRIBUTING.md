# Contributing to JevCompass

JevCompass is currently developed in a private repository. Invited collaborators can file issues or propose changes there; this document does not grant an open-source license. For security-sensitive reports, use a private channel with the maintainer and do not paste credentials or private logs into a public issue.

## Set up a local checkout

Python 3.11 or newer is required. Node and an OpenRouter key are not needed for the local test suite.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
PYTHONPATH=src python -m unittest discover -s tests -v
```

To inspect a distribution candidate, install the build frontend, build locally, and audit both archives:

```bash
python -m pip install build
python -m build
python tools/check_distribution.py
```

For a host check, use an isolated Codex profile (`CODEX_HOME`), run `jevcompass install --dry-run` before `jevcompass install`, then review `/hooks` and start a fresh session. Never overwrite another user's hooks to run a test. `doctor --test-jev` sends a synthetic billed request; regular unit tests and `doctor` do not require one. Keep backend tests synthetic unless the test explicitly calls for an authorized live request.

## Propose changes

- Describe the user task and what recommendation should improve. Include a case where the advisor should stay silent.
- Keep task classification local and send only generic, allowlisted candidate metadata to Decisions. Avoid raw prompts, paths, code, private skill descriptions and memory data in requests and diagnostic logs.
- Preserve unrelated hooks; advice must remain non-blocking and must not bypass Codex permission checks or required project tests.
- Add focused tests for changed hook, catalog, installer, or HTTP behavior. Check the package archive when adding bundled files.
- Distinguish a unit-test pass from live host delivery and measured task benefit. For new advice categories, compare equivalent tasks and record negative results too.

When filing a bug, include Codex host/version, OS, Python version, JevCompass version, the relevant category/domain, redacted `jevcompass doctor --json` output, and whether advice appeared before first tool use. Never attach raw hook payloads, secret-bearing environment values, private diffs, or customer data. See [ROADMAP.md](ROADMAP.md) for the outstanding host and efficacy checks.
