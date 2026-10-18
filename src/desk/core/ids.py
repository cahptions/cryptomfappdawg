"""Time-sortable unique identifiers (ULID layout: 48-bit milliseconds + 80 random bits, Crockford base32)."""

from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id(prefix: str = "", now_ms: int | None = None) -> str:
    """Return a 26-character ULID-style id, optionally prefixed (e.g. ``cand_01J…``)."""
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    if not 0 <= ms < 2**48:
        raise ValueError("timestamp out of range for ULID")
    value = (ms << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    ulid = "".join(reversed(chars))
    return f"{prefix}_{ulid}" if prefix else ulid
