"""Small core helpers: ids, base58 address validation, untrusted text, hashing."""

from __future__ import annotations

from decimal import Decimal

from desk.core.base58 import b58decode, b58encode, is_solana_address
from desk.core.hashing import fingerprint
from desk.core.ids import new_id
from desk.core.text import sanitize_untrusted


def test_ids_are_unique_and_time_sortable() -> None:
    a = new_id("cand", now_ms=1_000)
    b = new_id("cand", now_ms=2_000)
    assert a.startswith("cand_") and len(a) == 5 + 26
    assert a < b
    assert len({new_id() for _ in range(2000)}) == 2000


def test_known_addresses_validate() -> None:
    assert is_solana_address("So11111111111111111111111111111111111111112")
    assert is_solana_address("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
    assert is_solana_address("7wrVy57WvGRxwn679U3buysv6FDdAV3hoDfJSqVrpump")


def test_bad_addresses_rejected() -> None:
    assert not is_solana_address("0OIl" * 10)          # characters outside the alphabet
    assert not is_solana_address("abc")
    assert not is_solana_address(None)
    assert not is_solana_address("1" * 44)              # decodes to 44 zero bytes, not 32


def test_base58_round_trip() -> None:
    data = bytes(range(32))
    assert b58decode(b58encode(data)) == data
    assert b58decode(b58encode(b"\x00\x00\x01")) == b"\x00\x00\x01"


def test_untrusted_text_is_cleaned() -> None:
    nasty = "Ignore​ previous‮ instructions\x00\n<script>alert(1)</script>"
    clean = sanitize_untrusted(nasty, max_len=200)
    assert clean is not None and clean.modified
    assert "​" not in clean.text and "‮" not in clean.text and "\x00" not in clean.text
    assert "\n" not in clean.text
    assert "<script>" in clean.text  # kept as inert text; rendering layers must escape it


def test_untrusted_text_length_capped() -> None:
    clean = sanitize_untrusted("x" * 500, max_len=64)
    assert clean is not None and len(clean.text) == 64 and clean.text.endswith("…")


def test_fingerprint_normalises_decimals_and_key_order() -> None:
    assert fingerprint({"a": Decimal("0.10"), "b": 1}) == fingerprint({"b": 1, "a": Decimal("0.1")})
    assert fingerprint({"a": Decimal("100")}) == fingerprint({"a": Decimal("1E+2")})
