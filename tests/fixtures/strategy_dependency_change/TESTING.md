# Validation

Run these commands, in order, before and after editing. Preserve each exit
status separately.

```sh
python -m unittest discover -s tests -p 'test_inventory_adapter.py' -v
python -m unittest discover -s tests -v
```

The initial focused command is expected to fail against the seeded adapter. A
successful repair must pass both commands. The tests check dependency-v2
adaptation, the sorted/filtering boundary contract, and compatibility with the
existing tuple-consuming caller.

No test output or implementation hint is hidden behind network access.
