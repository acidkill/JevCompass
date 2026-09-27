# Synthetic dependency contract review task

This fictional app's adapter must preserve the stable public API described in
`API_CONTRACT.md` while adapting to the new `ledger_archive` v2 client shape.
The old dependency's positional lookup call is no longer supported. Work only in
`ledger_adapter.py`; do not change the contract, dependency fake, or tests.

Start with the existing fixture tests:

```sh
python -m unittest discover -s tests -p 'test_ledger_adapter.py' -v
python -m unittest discover -s tests -v
```

Run both commands in order and report their separate exit statuses. After they
pass, the supervisor independently runs
`scripts/verify_dependency_contract.py --fixture-dir <copy>`. It executes a
frozen 10-check suite in a time-bounded subprocess. The verifier reports only
aggregate counts and safe status, not assertion text or fixture output.

The verifier runs trusted synthetic fixture code; subprocess isolation and
timeout handling are not an operating-system security sandbox. No network,
external dependencies, sleeps, or real service calls are involved.
