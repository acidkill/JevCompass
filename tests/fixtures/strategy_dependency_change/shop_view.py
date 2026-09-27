"""Existing consumer of the application's stable row interface."""


def render_inventory(rows: list[tuple[str, int]]) -> str:
    return "\n".join(f"{code}: {quantity}" for code, quantity in rows)
