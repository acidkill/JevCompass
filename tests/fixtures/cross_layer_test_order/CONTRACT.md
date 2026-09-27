# Checkout service-fee contract

For a nonnegative integer subtotal in cents, calculate a service fee of exactly two percent, rounded to the nearest cent using half-up rounding. The payable total is subtotal plus that fee. Booleans and non-integers are invalid even though Python treats booleans as integers.

Successful CLI output is one JSON object on stdout with exactly these integer fields: `subtotal_cents`, `service_fee_cents`, and `total_cents`. Existing subtotal semantics and validation remain unchanged. Invalid input must exit nonzero and emit no JSON to stdout.

Examples:
- 25 cents subtotal -> 1 cent fee -> 26 cents total (the exact fee is one half-cent).
- 75 cents subtotal -> 2 cent fee -> 77 cents total (the exact fee is one and one-half cents).
- 1250 cents subtotal -> 25 cent fee -> 1275 cents total.
