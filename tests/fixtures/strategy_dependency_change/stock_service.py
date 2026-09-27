"""Local fake for the fictional upstream stock service v2."""
from dataclasses import dataclass


@dataclass(frozen=True)
class StockRecord:
    code: str
    units_available: int
    listed: bool


@dataclass(frozen=True)
class StockReport:
    records: tuple[StockRecord, ...]
    revision: str


def fetch_report() -> StockReport:
    """Return a representative immutable v2 report."""
    return StockReport(
        records=(
            StockRecord("B-2", 4, True),
            StockRecord("A-1", 2, True),
            StockRecord("X-9", 8, False),
            StockRecord("C-3", 0, True),
        ),
        revision="rev-12",
    )
