"""Roster domain transform and local JSON command-line adapter."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Any


@dataclass(frozen=True)
class Person:
    email: str
    name: str
    team: str


def import_roster(records: list[dict[str, Any]]) -> list[Person]:
    """Normalize roster rows and retain unique people."""
    people: list[Person] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("each roster entry must be an object")
        values = {}
        for key in ("email", "name", "team"):
            value = record.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a non-empty string")
            values[key] = value.strip()
        people.append(Person(
            email=values["email"].lower(),
            name=values["name"],
            team=values["team"],
        ))
    return sorted(people, key=lambda person: person.email)


def _json_payload(people: list[Person]) -> dict[str, Any]:
    return {"count": len(people), "people": [asdict(person) for person in people]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a roster JSON file")
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("input must be a JSON array")
        people = import_roster(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"roster input error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(_json_payload(people), separators=(",", ":"), sort_keys=True))
    return 0
