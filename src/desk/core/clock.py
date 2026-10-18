"""Time handling. Everything is timezone-aware UTC; naive datetimes are rejected at boundaries.

Two clocks are used:
* wall clock (``utc_now``) for timestamps that are stored and compared with provider data;
* monotonic clock (``monotonic``) for latency and staleness, so a wall-clock jump (NTP, laptop sleep)
  can never make stale data look fresh.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime

from desk.core.errors import DeskError

# Plausibility window for timestamps coming from providers. Anything outside is almost certainly a
# seconds/milliseconds mix-up or garbage, and is rejected instead of silently accepted.
_MIN_PLAUSIBLE = datetime(2015, 1, 1, tzinfo=UTC)
_MAX_PLAUSIBLE = datetime(2100, 1, 1, tzinfo=UTC)
_LONG_FRACTION = re.compile(r"(\.\d{6})\d+")  # providers send up to nanoseconds; keep microseconds


class TimestampError(DeskError):
    """A timestamp is naive, unparsable, or implausible."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def monotonic() -> float:
    return time.monotonic()


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` converted to UTC; raise if it is naive."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise TimestampError(f"naive datetime rejected: {value!r}")
    return value.astimezone(UTC)


def _check_plausible(value: datetime) -> datetime:
    if not (_MIN_PLAUSIBLE <= value < _MAX_PLAUSIBLE):
        raise TimestampError(f"implausible timestamp {value.isoformat()}")
    return value


def parse_iso8601(text: str) -> datetime:
    """Parse ISO-8601 such as ``2026-10-18T21:52:51Z``. A timezone is mandatory."""
    if not isinstance(text, str) or not text:
        raise TimestampError(f"not an ISO-8601 string: {text!r}")
    candidate = text.strip()
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    candidate = _LONG_FRACTION.sub(r"\1", candidate, count=1)
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise TimestampError(f"unparsable ISO-8601 timestamp {text!r}") from exc
    return _check_plausible(ensure_utc(parsed))


def _from_timestamp(seconds: float) -> datetime:
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise TimestampError(f"timestamp out of range: {seconds!r}") from exc


def from_epoch_seconds(value: int | float) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TimestampError(f"epoch seconds must be a number, got {value!r}")
    return _check_plausible(_from_timestamp(value))


def from_epoch_millis(value: int | float) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TimestampError(f"epoch milliseconds must be a number, got {value!r}")
    return _check_plausible(_from_timestamp(value / 1000.0))


def to_iso(value: datetime) -> str:
    """Canonical storage format: ``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``."""
    return ensure_utc(value).isoformat(timespec="microseconds")


def to_epoch_millis(value: datetime) -> int:
    return int(ensure_utc(value).timestamp() * 1000)
