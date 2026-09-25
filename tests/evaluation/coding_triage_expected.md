# Coding triage blind evaluation rubric (frozen)

## Paired-run setup

Use a fictional, byte-identical copy of
`tests/fixtures/ambiguous_import_triage/` for each arm. Randomize which arm
runs first, keep the model and settings identical, and anonymize arm labels
before evaluation. The task prompt is the fixture README, unchanged in both
arms. In the treatment only, after recording the initial focused failure, ask
JevCompass to rank these allowlisted candidates:

```sh
python -m jevcompass triage --exit-code 1 --kind import --hypothesis import_module_missing --hypothesis import_path_changed --json
```

The CLI recommendation does not execute tests or diagnostics. The agent must
perform its own read-only diagnostic and source edit. No key or live Jev call is
required for fixture validation.

## Frozen expected result (keep out of agent-facing prompt)

The initial focused command fails during test discovery while importing
`test_store.py`, with `ModuleNotFoundError: No module named 'parcelcache.codec'`. The package root
is present and importable, `parcelcache.codec` is absent, and
`parcelcache.wire` is present. The minimal repair is to change the import in
`parcelcache/api.py` from `.codec` to `.wire`; this preserves the public API
and makes both contract tests pass. This expected result is for evaluators and
must not be copied into the task prompt or pilot receipt.

A qualifying discriminator is a read-only standard-library check of
`importlib.util.find_spec("parcelcache")`,
`find_spec("parcelcache.codec")`, and
`find_spec("parcelcache.wire")`, run from the fixture root. Equivalent
read-only inspection of the local package tree is acceptable only when the
agent explicitly uses it to distinguish an absent package from an obsolete
submodule path. A generic traceback restatement or an unsupported guess does
not qualify.

## Blind scoring

Score each anonymous arm independently before revealing the arm map. Record
each item as 1 (met), 0 (not met), or U (unobservable):

1. **Diagnosis:** identifies stale internal import path as the cause and
   distinguishes it from an unavailable package with concrete local evidence.
2. **Diagnostic quality:** first diagnostic action is read-only, bounded, and
   actually discriminates the two hypotheses; no network, credentials, or
   unrelated files are accessed.
3. **Repair:** changes only the production import path (or an equivalent
   minimal fix); public `parcelcache.api.encode/decode` remains intact.
4. **Regression safety:** no tests are edited, removed, skipped, or weakened;
   no dependency or unrelated file changes are introduced.
5. **Focused validation:** the exact focused command from the prompt is
   observed after the edit and exits 0.
6. **Required validation:** the exact full-suite command from the prompt is
   observed and exits 0. This is a hard completion gate, reported separately
   from the summed quality score.

Do not give credit for a claimed command without event evidence of its
invocation and completion. Keep an exit status from every observed run; a CLI
advice result must never replace the test process status.

## Timing and privacy measures

Define the initial useful-error timestamp as milliseconds from the Codex
process start to the completion event for the first focused test-discovery
command whose bounded output matches both `ERROR: test_store
(unittest.loader._FailedTest.test_store)` and `No module named
'parcelcache.codec'`. A command start, assistant
statement, generic error, or test discovery with no matching failure is not a
useful-error event. Store only the elapsed numeric timestamp and a boolean
match in the receipt; retain no raw output, prompt, transcript, source, paths,
environment, command arguments, credentials, or model response. If output or
event timing is unavailable, record the timestamp as null/unscored.

Report per arm: blind score vector, exact focused/full invocation booleans and
exit codes, initial useful-error elapsed milliseconds (or null), process wall
time, and bounded token counters when valid. These are per-pair observations;
one pair is not evidence of a causal speed or cost effect.
