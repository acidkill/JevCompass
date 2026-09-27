# Synthetic condition timeout triage

A bounded consumer reports a timeout even though publication completed. Inspect the condition predicate, queue operations and test contract before deciding between resource contention and a wait condition that cannot become ready under the current publication path.

Change only inbox.py. Run focused `python -m unittest discover -s tests -p test_inbox.py -v`, then required full `python -m unittest discover -s tests -v`. Report actual exits; never reinterpret a timeout as success. Keep both commands separate even while they cover the same current tests.

This fixture has a locally inspectable defect. It supports correctness checks and local diagnostic routing, not proof of remote decision value. No raw code, paths or failure output should be sent to the advisor; any future call uses only verified allowlisted observations. No model-backed comparison has run.
