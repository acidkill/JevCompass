# Synthetic stock dependency migration task

The upstream stock service has changed its return shape. Adapt the existing
application boundary in `inventory_adapter.py` while preserving what its
current caller expects.

Before editing, read `CONTRACT.md`, inspect the dependency fake and caller, and
run the focused test command in `TESTING.md`. Work only in
`inventory_adapter.py`. The contract, dependency service, caller, and tests
are read-only task evidence.

The expected behavior is fully specified in the contract and checked by the
tests. This is a local-resolution control; it does not establish a genuine
unresolved strategy choice.

The fixture is synthetic, self-contained, and standard-library-only. It uses no
network, external packages, private paths, or real services.
