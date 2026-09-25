# ParcelQuote command contract

Invoke the package as `python -m parcelquote --weight-grams N --zone Z`.

- `N` must be a positive integer number of grams.
- `Z` is either `near` or `far`.
- A started kilogram is `ceil(N / 1000)`; thus 1000 grams is one kilogram and
  1001 grams is two.
- The near-zone base charge is 250 cents; the far-zone base charge is 700 cents.
- Each started kilogram adds 125 cents.
- On success, stdout is one JSON object with `weight_grams`, `zone`,
  `billable_kg`, and `total_cents` fields. Do not add prose to stdout.
- Invalid input exits nonzero and writes a useful message to stderr.

Examples: 1000 grams to `near` is 375 cents; 1001 grams to `near` is 500
cents. The tests are the executable contract for exact field values.
