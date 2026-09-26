# ParcelQuote money representation

Extend successful CLI JSON with `money`, an object containing exactly `currency: "USD"` and `minor_units`, equal to the existing integer `total_cents`. Preserve all existing fields, started-kilogram calculations, zone charges and input validation. No floating-point dollar conversion. Success writes one JSON object and exits zero; invalid input writes no JSON and exits nonzero. Only the public serialization requires a change; calculation rules are already correct.
