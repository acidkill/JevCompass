# ParcelQuote validation

After a code change, choose a focused check to run first from the two useful candidates below, then run the full required suite. Both candidates exercise the started-kilogram boundary through different interfaces; neither replaces the full suite.

- Unit: `python -m unittest discover -s tests -p 'test_unit*.py' -v`
- Public CLI contract: `python -m unittest discover -s tests -p 'test_contract*.py' -v`
- Required final gate: `python -m unittest discover -s tests -v`

The optional machine-readable copy of these candidates is `test-options.json`.

Report the commands and their exit statuses. A failing test remains a failure until the cause is fixed and the checks are rerun.
