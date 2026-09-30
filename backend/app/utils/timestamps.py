"""UTC timestamps for API responses.

Models store times in UTC, but SQLite drops the zone on the way back, so a
stored value can come back naive. Serialising that as-is ("2026-09-30T19:19:50")
makes browsers read it as local time. Everything the API returns goes through
here so it always carries an explicit UTC offset.
"""

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator


def as_utc(value: datetime) -> datetime:
    """Return value as an aware UTC datetime; a naive value is taken to be UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    """ISO 8601 string with a UTC offset, or None."""
    return as_utc(value).isoformat() if value is not None else None


UtcDatetime = Annotated[datetime, AfterValidator(as_utc)]
"""Pydantic field type that always serialises with a UTC offset."""
