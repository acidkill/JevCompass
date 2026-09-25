# Contributing to JevCompass

Thanks for helping make Codex tool and skill selection more useful. Contributions to this project are provided under the [Apache License 2.0](LICENSE). Open an issue for a reproducible problem or a focused pull request for a change. For security-sensitive reports, contact the maintainer privately through GitHub; never post credentials, private prompts, or raw hook logs in a public issue.

## First contribution

1. Read the [README](README.md) for the product, current install path, privacy boundary, and limitations.
2. Check [open work and release evidence](TASKS.md) and the [acceptance pilot](PILOT.md) before proposing a new claim or behavior.
3. Use the local checkout and test commands below. Keep examples synthetic; do not include a real prompt, codebase, paths, memory, or credentials in a report.

Start with a small, synthetic example showing when JevCompass should advise and when it should stay quiet. This keeps review focused and avoids disclosing real project content.

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

## Pull request gate

Work on a focused branch and open a pull request. The single `ubuntu-slim` CI job runs the complete Python `unittest` suite on Python 3.11 for each pull request. Distribution checks remain a separate release validation. Merge only when every required CI check is green; local tests alone do not satisfy this gate. Python 3.12–3.14 and macOS checks are release validation, not a six-runner matrix on every change. If GitHub stops a job before its first step, keep the PR unmerged and report that account-side runner status separately from a test failure.

## Propose changes

- Describe the user task and what recommendation should improve. Include a case where the advisor should stay silent.
- Keep task classification local and send only generic, allowlisted candidate metadata to Decisions. Avoid raw prompts, paths, code, private skill descriptions and memory data in requests and diagnostic logs.
- Preserve unrelated hooks; advice must remain non-blocking and must not bypass Codex permission checks or required project tests.
- Add focused tests for changed hook, catalog, installer, or HTTP behavior. Check the package archive when adding bundled files.
- Distinguish a unit-test pass from live host delivery and measured task benefit. For new advice categories, compare equivalent tasks and record negative results too.

When filing a bug, include Codex host/version, OS, Python version, JevCompass version, the relevant category/domain, redacted `jevcompass doctor --json` output, and whether advice appeared before first tool use. Use the repository issue forms; for current claims and open validation, see [PILOT.md](PILOT.md) and [TASKS.md](TASKS.md). Never attach raw hook payloads, secret-bearing environment values, private diffs, or customer data. See [ROADMAP.md](ROADMAP.md) for the outstanding host and efficacy checks.
