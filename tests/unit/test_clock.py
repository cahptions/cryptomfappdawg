"""M1-09: timestamps normalise to tz-aware UTC; naive datetimes are rejected."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from desk.core.clock import (
    TimestampError,
    ensure_utc,
    from_epoch_millis,
    from_epoch_seconds,
    parse_iso8601,
    to_epoch_millis,
    to_iso,
)

EXPECTED = datetime(2026, 10, 18, 21, 52, 51, tzinfo=UTC)


def test_iso_with_z() -> None:
    assert parse_iso8601("2026-10-18T21:52:51Z") == EXPECTED


def test_iso_with_offset_converted_to_utc() -> None:
    parsed = parse_iso8601("2026-10-18T23:52:51+02:00")
    assert parsed == EXPECTED and parsed.tzinfo == UTC


def test_epoch_seconds_and_millis_agree() -> None:
    assert from_epoch_seconds(1792360371) == from_epoch_millis(1792360371000)


def test_dexscreener_pair_created_at_millis() -> None:
    assert from_epoch_millis(1792360340000).year == 2026


def test_seconds_passed_as_millis_is_rejected_as_implausible() -> None:
    with pytest.raises(TimestampError):
        from_epoch_millis(1792360371)  # seconds by mistake -> 1970


def test_millis_passed_as_seconds_is_rejected() -> None:
    with pytest.raises(TimestampError):
        from_epoch_seconds(1792360371000)


@pytest.mark.parametrize("text", ["2026-10-18T21:52:51", "yesterday", ""])
def test_naive_or_garbage_iso_rejected(text: str) -> None:
    with pytest.raises(TimestampError):
        parse_iso8601(text)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(TimestampError):
        ensure_utc(datetime(2026, 10, 18, 12, 0, 0))


def test_storage_round_trip() -> None:
    value = datetime(2026, 10, 18, 21, 52, 51, 123456, tzinfo=timezone(timedelta(hours=-5)))
    assert parse_iso8601(to_iso(value)) == value
    assert to_epoch_millis(EXPECTED) == 1792360371000


def test_bool_is_not_a_timestamp() -> None:
    with pytest.raises(TimestampError):
        from_epoch_seconds(True)
