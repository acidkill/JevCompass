"""Minimal CLI whose help text is the install/documentation authority."""
from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(prog="tinytext", description="Normalize text files")
    parser.add_argument("input", nargs="?", help="text file to normalize")
    parser.add_argument("--check", action="store_true", help="check without writing changes")
    parser.parse_args()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
