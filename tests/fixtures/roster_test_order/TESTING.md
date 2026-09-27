# Validation commands

Optional focused candidates are exposed with identical metadata to both arms:

- Unit: `python -m unittest discover -s tests -p 'test_unit*.py' -v` — fast transformation and boundary checks. It directly covers first-seen deduplication.
- Integration: `python -m unittest discover -s tests -p 'test_integration*.py' -v` — invoke the public CLI and check JSON serialization and invalid-input behavior. Its unique, already-ordered input isolates adapter mapping from duplicate handling.

Mandatory full suite after a source change:

- `python -m unittest discover -s tests -v`

The frozen independent oracle runs after the required suite in each arm. It invokes the CLI with duplicate mixed-case emails, an empty list, and verifies the first record and first-seen ordering without importing domain functions. The unit and integration candidates detect different seeded defects; both remain mandatory through the full suite. Targets are curated metadata, not exhaustive coverage claims: integration exercises the transformed result as well as public serialization.

On the exact runtime-relevant fixture inputs pinned in `runtime-samples.json`, five alternating local subprocess runs measured unit 54.84–60.28 ms (median 58.23 ms) and integration 154.46–176.86 ms (median 163.90 ms), including interpreter startup. Both candidates exited 1 on the seeded implementation. These are local timing observations only; they do not establish model benefit or predict another machine's timings.

Both arms receive identical task facts, fixture, test metadata, model/settings, and credential availability. The baseline may choose either candidate. The treatment must request exactly one rank after a meaningful edit to `roster/service.py` and before its first focused check, to establish treatment exposure. This requires asking for advice, not following it. The treatment may reject the order or use a local choice; abstention and adoption are recorded separately. Always run the required full suite and preserve failures. Receipts contain bounded status/numeric metadata only; do not publish prompts, test output, source, local paths, credentials, or raw logs.
