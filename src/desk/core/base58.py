"""Minimal base58 (Bitcoin alphabet) used to validate Solana addresses without extra dependencies."""

from __future__ import annotations

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}


def b58decode(text: str) -> bytes:
    if not text:
        raise ValueError("empty base58 string")
    value = 0
    for char in text:
        if char not in _INDEX:
            raise ValueError(f"invalid base58 character {char!r}")
        value = value * 58 + _INDEX[char]
    body = value.to_bytes((value.bit_length() + 7) // 8, "big") if value else b""
    leading_zeros = len(text) - len(text.lstrip("1"))
    return b"\x00" * leading_zeros + body


def b58encode(data: bytes) -> str:
    value = int.from_bytes(data, "big")
    out = []
    while value:
        value, rem = divmod(value, 58)
        out.append(_ALPHABET[rem])
    leading_zeros = len(data) - len(data.lstrip(b"\x00"))
    return "1" * leading_zeros + "".join(reversed(out))


def is_solana_address(text: object) -> bool:
    """True if ``text`` is base58 that decodes to exactly 32 bytes (a public key or program address)."""
    if not isinstance(text, str) or not 32 <= len(text) <= 44:
        return False
    try:
        return len(b58decode(text)) == 32
    except ValueError:
        return False
