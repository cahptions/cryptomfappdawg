"""Shared helpers for turning raw provider JSON into contracts.

Parsers are pure functions (bytes in, contracts out) so they can be tested against stored real responses.
A malformed *response* raises :class:`SchemaError`. A malformed *item* inside an otherwise valid list is
skipped and reported in :attr:`ParseResult.errors` — an object is either fully valid or not produced at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import ValidationError

from desk.contracts.observed import DataStatus, Observed
from desk.core.clock import TimestampError, from_epoch_millis, parse_iso8601
from desk.core.errors import SchemaError, UnitError
from desk.core.text import sanitize_untrusted
from desk.core.units import to_decimal

T = TypeVar("T")

# Exceptions that mean "this item does not match the verified schema".
ITEM_ERRORS = (KeyError, TypeError, ValueError, AttributeError, IndexError, UnitError, TimestampError,
               ValidationError, SchemaError)


@dataclass
class ParseResult(Generic[T]):
    items: list[T] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _reject_constant(name: str) -> Any:
    raise SchemaError(f"non-finite JSON constant {name} rejected")


def load_json(body: bytes | str) -> Any:
    """Parse JSON with exact decimals (no binary floats) and no NaN/Infinity."""
    try:
        return json.loads(body, parse_float=Decimal, parse_constant=_reject_constant)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SchemaError(f"response is not valid JSON: {exc}") from exc


def expect_dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{what}: expected an object, got {type(value).__name__}")
    return value


def expect_list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise SchemaError(f"{what}: expected a list, got {type(value).__name__}")
    return value


def opt_decimal(value: Any) -> Decimal | None:
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    return to_decimal(value)


def opt_int(value: Any) -> int | None:
    if value is None:
        return None
    return strict_int(value)


def strict_int(value: Any) -> int:
    """Accept ints or digit strings (Solana amounts are often strings); reject fractions and bools."""
    if isinstance(value, bool):
        raise SchemaError(f"boolean where an integer was expected: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    raise SchemaError(f"not an integer: {value!r}")


def opt_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise SchemaError(f"not a boolean: {value!r}")
    return value


def opt_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SchemaError(f"not a string: {value!r}")
    return value or None


def opt_iso(value: Any) -> datetime | None:
    return None if value is None else parse_iso8601(value)


def opt_epoch_ms(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        value = int(value)
    return from_epoch_millis(value)


def untrusted(value: Any, max_len: int, flags: list[str], flag: str) -> str | None:
    clean = sanitize_untrusted(value, max_len=max_len)
    if clean is None:
        return None
    if clean.modified:
        flags.append(flag)
    return clean.text or None


def observed_decimal(value: Any, source: str, observed_at: datetime, flags: tuple[str, ...] = ()) -> Observed[Decimal]:
    """OK when the provider gave a number, MISSING when it gave null/omitted it. Never zero-filled."""
    parsed = opt_decimal(value)
    if parsed is None:
        return Observed[Decimal](value=None, status=DataStatus.MISSING, source=source, observed_at=observed_at,
                                 flags=flags)
    return Observed[Decimal](value=parsed, status=DataStatus.OK, source=source, observed_at=observed_at, flags=flags)


@dataclass
class Fetched(Generic[T]):
    """Parsed items from one provider response, plus per-item errors and the response's evidence hash."""

    items: list[T]
    errors: list[str]
    raw_sha256: str
    status: int
    latency_ms: Decimal
