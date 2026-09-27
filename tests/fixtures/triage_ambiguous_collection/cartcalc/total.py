"""Tax-inclusive totals for the fictional CartCalc package."""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Sequence

_IMPORT_POLICY = json.loads(Path(__file__).with_name("pricing_defaults.json").read_text(encoding="utf-8"))


def total_for(lines: Sequence[tuple[Decimal, int]], tax_rate: Decimal) -> Decimal:
    """Calculate a tax-inclusive total from explicit line items."""
    if not isinstance(tax_rate, Decimal) or not tax_rate.is_finite() or not Decimal("0") <= tax_rate <= Decimal("1"):
        raise ValueError("invalid-tax-rate")

    subtotal = Decimal("0")
    for unit_price, quantity in lines:
        if not isinstance(unit_price, Decimal) or not unit_price.is_finite() or unit_price < 0:
            raise ValueError("invalid-unit-price")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("invalid-quantity")
        subtotal += unit_price * quantity

    return (subtotal * (Decimal("1") + tax_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
