---
name: jevcompass-coding-workflow
description: Optional workflow for coding tasks with a genuinely unresolved implementation choice, competing focused checks, or an observed failure with competing explanations.
---

# Coding workflow with JevCompass

Use these steps only when the corresponding phase is reached. Inspect the repository, contract, callers, tests, and required validation locally first. If local evidence makes the choice clear, follow it directly and skip JevCompass. A JevCompass result is advisory: it never edits code, runs tests, establishes a failure cause, grants permission, or replaces required checks.

Only run a command that may use the configured decision service when that use is permitted for the task. Keep prompts, source, diffs, paths, test output, logs, exception text, credentials, and private names out of JevCompass inputs. Do not infer or invent local evidence.

## Before editing: choose a strategy only if approaches remain plausible

For a substantial coding change, first inspect the relevant interfaces and evidence. If at least two materially different implementation approaches still fit, pass only the task kind and verified coarse signals:

```sh
jevcompass strategy choose --kind coding \
  --signal existing_symbol \
  --signal behavior_change \
  --json
```

The CLI accepts only allowlisted kinds, signals, and strategy IDs; it takes no prompt, code, path, or log text. A `remote-choice` is an accepted ranking of reviewed strategies. A `no-remote-choice` is a normal local fallback; use the local evidence and continue. If an approach is already established, do not call the selector just to confirm it. An integration may pass `--resolved-strategy` only when the caller has independently verified that strategy from local evidence; this route is local and does not request an API decision.

## After editing: rank focused checks only when their order is genuinely unclear

Read the project’s test instructions and CI to identify the exact mandatory checks. When verified local timings show that the complete required suite is already cheap and covers the change, and no separate focused check is required, run the exact full suite directly and skip ranking its subsets. Keep every separately required validation step. This does not alter frozen experiment gates or justify reclassifying earlier results. Select a focused check from the changed behavior and its existing tests. If multiple materially different focused checks remain plausible and you have verified the coarse metadata, you may rank them with `tests rank`. Replace the illustrative commands below with real local commands. Put the repository’s actual required command in `required`; it is returned unchanged and still has to be run.

```json
{
  "surface": "python",
  "signals": ["internal_logic_changed"],
  "candidates": [
    {
      "id": "focused-unit",
      "kind": "unit",
      "command": "python -m unittest tests.test_example -v",
      "relevance": 0.5,
      "coverage": "direct",
      "runtime": "fast",
      "coverage_targets": ["internal_logic_changed"]
    },
    {
      "id": "focused-contract",
      "kind": "contract",
      "command": "python -m unittest tests.test_example_contract -v",
      "relevance": 0.5,
      "coverage": "direct",
      "runtime": "unknown",
      "coverage_targets": ["public_contract_changed"]
    }
  ],
  "required": [
    {
      "id": "repository-required",
      "command": "python -m unittest discover -s tests -v"
    }
  ]
}
```

Save the metadata locally, then run:

```sh
jevcompass tests rank --input test-order.json --json
```

Keep the metadata bounded to verified enum fields and generic values. Candidate IDs and command strings stay local; only coarse selection metadata may be sent for an eligible decision. Never encode private details in the surface or other fields. The command prints an order; it does not execute candidate or required commands. If it abstains, use your local ordering. Run the chosen focused check first. If it fails, inspect the observed failure, make an authorized repair, and rerun the affected check. Then run every mandatory repository check even if the focused check failed or JevCompass ranked it last. Report each actual command and exit status.

## After a real failure: request a diagnostic step only if explanations remain

Run the check locally first and retain its actual exit code. Inspect the failure and perform a cheap, read-only discriminator where one is available. If at least two evidence-backed hypotheses still fit, pass only the observed exit code and allowlisted failure/hypothesis IDs. For example:

```sh
jevcompass triage --exit-code 1 --kind import \
  --hypothesis import_module_missing \
  --hypothesis import_path_changed \
  --json
```

The CLI accepts enum values, not the failure text, code, path, or log. Add an observation flag only when that exact enum fact was checked locally. Use the returned locally authored step to guide your next investigation. A suggested step is not a confirmed cause or an automatic fix, and the original test failure remains a failure until the test is rerun and passes. If local inspection leaves only one plausible explanation, investigate it directly and skip triage.

When relative check effort has been established locally and competing diagnostics remain useful, you may add repeatable `--diagnostic-cost HYPOTHESIS=COST` options for supplied hypothesis IDs. Costs are `low`, `medium`, `high`, or `unknown`. Omit unverified estimates; do not fabricate costs to steer the result. For example, append `--diagnostic-cost import_module_missing=low` only if the missing-package check is verified to be low effort in this task. Cost guides diagnostic order, not causal likelihood. A cheap decisive local check should be performed directly rather than requesting remote confirmation.

If you opt into `--rank-hypotheses`, assess the complete causal order separately from the selected next diagnostic step. An inexpensive check is not evidence that its associated cause is more likely. Unknown costs, incomplete rankings or backend failure do not remove any required tests; continue with the normal local investigation.
