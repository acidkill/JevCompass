# Synthetic Checkout service-fee task

Add a two-percent checkout service fee, rounded half up to the nearest cent. Preserve the existing subtotal and validation behavior. Include the fee in the total and add `service_fee_cents` to successful JSON output.

Keep all task-related behavior changes in `checkout/service.py`. Do not edit tests or contract documents, add dependencies, or access the network.

Start with either optional candidate:
- `python -m unittest discover -s tests -p 'test_unit*.py' -v`
- `python -m unittest discover -s tests -p 'test_integration*.py' -v`

After a source change, run one focused candidate and then the full required suite:
`python -m unittest discover -s tests -v`

Report the commands and exit statuses. A focused pass does not replace the full suite.
