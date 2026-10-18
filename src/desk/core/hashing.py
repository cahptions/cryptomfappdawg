"""Canonical hashing so identical inputs always produce identical fingerprints."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any


def _default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        # normalise so "0.10" and "0.1" hash identically
        return format(obj.normalize(), "f") if obj == obj.to_integral_value() else str(obj.normalize())
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, set | frozenset):
        return sorted(obj)
    raise TypeError(f"cannot canonicalise {type(obj).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def fingerprint(value: Any) -> str:
    return sha256_hex(canonical_json(value))
