from __future__ import annotations

from datetime import datetime


def parse_iso(value: str | None) -> datetime | None:
    """ISO-8601 string -> datetime; None/empty -> None; malformed -> ValueError."""
    return datetime.fromisoformat(value) if value else None
