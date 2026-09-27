---
name: jevcompass-focused-tests
description: Select and run focused tests after a code change, then complete repository-required validation.
---

# Focused tests after a change

1. Read the applicable repository instructions and test configuration. Check the project docs or CI for required checks; do not assume a familiar command is correct for this repository.
2. Inspect the change and its callers, interfaces, and affected behavior. Find existing tests that exercise those paths, then choose the smallest relevant test selection that can detect a regression.
3. If coverage evidence already identifies the first check, run it directly: do not issue a JevCompass command merely to confirm your selection. For a serialization or boundary-mapping change, prefer the test that directly asserts the changed public output when alternatives only cover unchanged calculations and no measured runtime tradeoff remains. If multiple materially different candidates remain plausible, optional JevCompass ranking can help; use only verified coarse coverage/runtime metadata and preserve unknown facts. Skip ranking when preparing or invoking it is more work than making the choice locally. Advice does not execute tests or replace evidence.
4. Choose validation proportionate to the change. If the repository does not require a separate focused check, and verified local timings show that the complete required suite is already cheap and covers the changed behavior, run that exact full suite directly without ranking or duplicating its focused subset. Preserve all separately required commands and checks. A frozen experiment that requires a focused invocation still requires it; never revise its gates after observing a result. Otherwise, run the focused selection first. Use the repository's documented runner and report the exact command and its real exit status. If it fails, inspect the observed failure, repair within the authorized scope and rerun the affected check; do not stop with an unverified final edit or call a failing check a success. A failed focused check does not cancel the required validation below. If repair cannot be completed, preserve the failure and report the unresolved work.
5. Copy the exact required test command from local CI or project instructions, including its flags, and run it after the focused check when using that path. A completed direct full-suite run on the final code can satisfy that same gate without a duplicate invocation; other required checks still run. Do not treat a focused pass as a substitute for a mandatory gate.
6. Report what ran, what passed or failed, and any checks not run with the reason. A started command is not a passing check; never infer success from partial output or an earlier run with different inputs.

Avoid broad test runs unrelated to the change when they are not required. If the change has no testable behavior, say why tests are unnecessary and still follow any required repository checks.
