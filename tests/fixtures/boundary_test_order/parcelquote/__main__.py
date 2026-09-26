"""JSON command-line interface for ParcelQuote."""

import argparse
import json
import sys

from .quote import quote_shipping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parcelquote")
    parser.add_argument("--weight-grams", required=True, type=int)
    parser.add_argument("--zone", required=True, choices=("near", "far"))
    args = parser.parse_args(argv)
    try:
        quote = quote_shipping(args.weight_grams, args.zone)
    except (TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps({
        "weight_grams": quote.weight_grams,
        "zone": quote.zone,
        "billable_kg": quote.billable_kg,
        "total_cents": quote.total_cents,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
