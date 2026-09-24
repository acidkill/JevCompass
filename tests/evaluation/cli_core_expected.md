# CLI core fixture expected checks

This is the evaluator checklist for the synthetic P01, P03, P05, P07, and
R01-R06 cases. It is kept outside the temporary project copied for each arm.

| Case | Expected check |
|---|---|
| P01 | `normalize_whitespace("") == ""`; `normalize_whitespace("  red   fox  ") == "red fox"`; run `python -m unittest discover -s tests` successfully. |
| P03 | Remove reliance on an unset `OUTPUT_PATH` (for example, accept/validate an argument or provide a safe default); `bash -n scripts/render_report.sh` exits 0. |
| P05 | README install instructions use the current `pyproject.toml`/Python package workflow, document `python -m tinytext --help` and `python -m unittest discover -s tests`, and agree with the actual help and tests. |
| P07 | Contract defines method/path, JSON content, required and optional request fields, validated success response, and explicit client-input and server-error cases. |
| R01 | `git branch --show-current` prints `fixture-main`. |
| R02 | `find . -maxdepth 1 -type f` reports exactly `AGENTS.md`, `API_REQUIREMENTS.md`, `README.md`, and `pyproject.toml` (four files). |
| R03 | `test -f README.md` succeeds. |
| R04 | `wc -c < pyproject.toml` reports the byte size of the fixture's unchanged `pyproject.toml`. |
| R05 | `find . -maxdepth 2 -type f -name '*.py'` lists `tinytext/__init__.py`, `tinytext/__main__.py`, `tinytext/cli.py`, `tinytext/text.py`, and `tests/test_text.py`. |
| R06 | `grep -q timeout README.md` succeeds. |

The P01 starter intentionally fails its desired-behavior assertions. The P03
script is syntactically valid but fails when run with no `OUTPUT_PATH`; P03's
specified syntax check is safe and does not execute it. Case checks describe
expected task outcomes and do not imply any Codex arm or JevCompass delivery was
run.
