"""Small schema boundaries for untrusted provider payloads. Error messages never echo payload values."""

from __future__ import annotations

import json
import math
import zipfile
from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from desk.core.errors import SchemaError

P = ParamSpec("P")
T = TypeVar("T")


def schema_boundary(source: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        @wraps(function)
        def parse(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return function(*args, **kwargs)
            except (ValueError, KeyError, TypeError, AttributeError, IndexError, OverflowError, UnicodeError,
                    zipfile.BadZipFile) as exc:
                raise SchemaError(f"{source}: malformed response; values suppressed") from exc
        return parse
    return decorate


def json_object(body: bytes) -> dict[str, Any]:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return dict(value)


def number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean measurement")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError("invalid numeric measurement")
    return result


def clean_text(value: Any, limit: int) -> str | None:
    """Display text from a provider: plain string, whitespace collapsed, bounded; anything else is None."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text or None


def web_url(value: Any) -> str | None:
    """An http(s) link to show as-is (never ``javascript:``, ``data:`` or a relative path); otherwise None."""
    from urllib.parse import urlsplit

    if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    parts = urlsplit(value)
    return value if parts.scheme in ("http", "https") and parts.hostname else None


def iso_day(value: Any) -> str | None:
    """``YYYY-MM-DD`` from a date or timestamp string, validated; otherwise None."""
    from datetime import date

    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None
