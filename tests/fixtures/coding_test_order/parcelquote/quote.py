"""Core shipping quote rules."""

from dataclasses import dataclass


BASE_CHARGE_CENTS = {"near": 250, "far": 700}
PER_KILOGRAM_CENTS = 125


@dataclass(frozen=True)
class Quote:
    weight_grams: int
    zone: str
    billable_kg: int
    total_cents: int


def quote_shipping(weight_grams: int, zone: str) -> Quote:
    """Return a quote, charging by whole kilograms."""
    if isinstance(weight_grams, bool) or not isinstance(weight_grams, int):
        raise TypeError("weight_grams must be an integer")
    if weight_grams <= 0:
        raise ValueError("weight_grams must be positive")
    if zone not in BASE_CHARGE_CENTS:
        raise ValueError("zone must be 'near' or 'far'")

    # Seeded defect: a remainder is currently ignored rather than billed as a
    # started kilogram. The exercise asks for this rounding rule to be fixed.
    billable_kg = weight_grams // 1000
    total_cents = BASE_CHARGE_CENTS[zone] + billable_kg * PER_KILOGRAM_CENTS
    return Quote(weight_grams, zone, billable_kg, total_cents)
