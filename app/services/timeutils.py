"""Time helpers. Everything internal is naive UTC (see app.models.common.utcnow)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.common import utcnow

__all__ = ["utcnow", "from_unix", "to_unix", "parse_iso"]


def from_unix(value: int | str | float | None) -> datetime | None:
    """Convert a Meta unix timestamp (seconds) to naive UTC."""

    if value is None or value == "":
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=UTC).replace(tzinfo=None)


def to_unix(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt
