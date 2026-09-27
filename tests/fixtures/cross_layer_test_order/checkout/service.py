"""Checkout domain calculation and its public JSON adapter."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import sys


@dataclass(frozen=True)
class Checkout:
    subtotal_cents: int
    service_fee_cents: int
    total_cents: int


def _service_fee_cents(subtotal_cents: int) -> int:
    """Return the two-percent fee rounded half up; seeded behavior is incomplete."""
    return 0  # Seeded implementation is missing the newly specified fee.


def quote_checkout(subtotal_cents: int) -> Checkout:
    """Calculate the checkout total from a subtotal expressed in cents."""
    if isinstance(subtotal_cents, bool) or not isinstance(subtotal_cents, int):
        raise TypeError("subtotal_cents must be an integer")
    if subtotal_cents < 0:
        raise ValueError("subtotal_cents must be nonnegative")

    fee = _service_fee_cents(subtotal_cents)
    return Checkout(subtotal_cents, fee, subtotal_cents + fee)


def _json_payload(quote: Checkout) -> dict[str, int]:
    """Adapt a domain quote to the documented public JSON object."""
    return {
        "subtotal_cents": quote.subtotal_cents,
        "total_cents": quote.total_cents,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="checkout")
    parser.add_argument("--subtotal-cents", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        quote = quote_checkout(args.subtotal_cents)
    except (TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(_json_payload(quote), sort_keys=True))
    return 0
