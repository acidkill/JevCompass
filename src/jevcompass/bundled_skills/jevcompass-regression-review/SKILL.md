---
name: jevcompass-regression-review
description: Review a code diff for regressions using affected callers, contracts, and failure paths as evidence.
---

# Regression review

1. Read applicable repository instructions and inspect the complete diff. Trace changed behavior through relevant callers and tests; check public contracts, persisted formats, and compatibility assumptions touched by the change.
2. Follow important success and failure paths, including invalid input and error handling where relevant. Look for behavior that could break existing callers or leave partial state.
3. Report only actionable findings, ordered by severity. For each, give the file and line, the concrete triggering condition, and the evidence for the impact.
4. Separate confirmed defects from questions or unverified risks. If no actionable issue is supported by the diff, say so; do not invent defects or claim untested behavior is safe.

Keep the review scoped to regressions introduced by the change. Do not present style preferences as correctness findings.
