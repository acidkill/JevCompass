---
name: jevcompass-focused-tests
description: Select and run focused tests after a code change, then complete repository-required validation.
---

# Focused tests after a change

1. Read the applicable repository instructions and test configuration. Check the project docs or CI for required checks; do not assume a familiar command is correct for this repository.
2. Inspect the change and its callers, interfaces, and affected behavior. Find existing tests that exercise those paths, then choose the smallest relevant test selection that can detect a regression.
3. Run that focused selection first. Use the repository's documented runner and report the exact command and its real exit status.
4. Copy the exact required test command from local CI or project instructions, including its flags, and run it after the focused check. Do not treat a focused pass as a substitute for a mandatory gate.
5. Report what ran, what passed or failed, and any checks not run with the reason. A started command is not a passing check; never infer success from partial output or an earlier run with different inputs.

Avoid broad test runs unrelated to the change when they are not required. If the change has no testable behavior, say why tests are unnecessary and still follow any required repository checks.
