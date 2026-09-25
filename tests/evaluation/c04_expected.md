# C04 blinded review rubric

The fixture is synthetic. Give the evaluator only anonymous final answer files and the three fixture files, never `mapping.json` or arm metadata until scoring is locked. Score findings independently of whether an advice ID appears.

Award one point per distinct, actionable defect correctly tied to a line, contract impact, and focused test:

1. `retry.py` catches every `Exception` (line 23), so a programming error such as `ValueError` is retried instead of propagated immediately. A fake `send` that raises `ValueError` with an attempt counter should be called exactly once, with no sleep.
2. `retry.py` always sleeps `base_delay` for retryable HTTP responses (line 29) and ignores numeric `Retry-After`. A first 429 response with `headers={"Retry-After": "2"}` followed by success should sleep exactly `[2.0]`.
3. `retry.py` sleeps on the final 429/5xx response (line 29 before final-attempt guard at line 30). A `max_attempts=1` 503 response should return it with zero calls to fake sleep.

Reject unsupported findings, changed semantics of `max_attempts`, and claims of running checks without evidence. Note any required-contract omissions, file/line accuracy, clarity, unnecessary work, and whether the response proposes concrete tests. The first productive action is the first successful read of `retry.py`, `RETRY_CONTRACT.md`, or `tests/test_retry.py`; reporting an advice ID is not productive action. Record candidate skill reads separately from any claim that instructions were applied. A single pair is descriptive, not proof of benefit. This case is outside the frozen 20-case acceptance denominator.
