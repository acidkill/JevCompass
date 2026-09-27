"""Stable application-facing stock adapter, seeded with a v1 assumption."""

from stock_service import fetch_report


def list_sellable() -> list[tuple[str, int]]:
    """Return available listed stock in the legacy application shape."""
    # Seeded migration defect: v2 returns a report object, not v1 row pairs.
    return sorted(
        (code, quantity)
        for code, quantity in fetch_report()
        if quantity > 0
    )
