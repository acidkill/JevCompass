# Validation

After editing run one focused suite, then always run the complete suite. Do not modify tests or CONTRACT.md.

- Unit: `python -m unittest discover -s tests -p 'test_unit*.py' -v`; indirect regression coverage of the unchanged calculation.
- Contract: `python -m unittest discover -s tests -p 'test_contract*.py' -v`; direct coverage of the new public mapping.
- Required: `python -m unittest discover -s tests -v`.

Runtime buckets remain unknown until repeated comparable local measurements establish a reproducible difference. Both arms receive the same measured metadata. Do not add sleeps or workloads to manufacture a difference.
