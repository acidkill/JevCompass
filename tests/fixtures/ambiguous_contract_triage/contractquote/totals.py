"""Invoice total calculation for the synthetic fixture."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def _round_to_cent(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def rounded_line_amounts(amounts: list[Decimal]) -> list[Decimal]:
    """Return cent-rounded values for display on each invoice line."""
    return [_round_to_cent(amount) for amount in amounts]


def invoice_total(amounts: list[Decimal]) -> Decimal:
    """Sum the displayed, rounded line amounts."""
    return sum(rounded_line_amounts(amounts), Decimal("0.00"))
