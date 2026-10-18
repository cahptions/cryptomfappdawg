"""Exact unit conversions. Money never touches binary floating point.

* SOL amounts are stored as integer lamports (1 SOL = 1,000,000,000 lamports).
* Token amounts are stored as integer raw base units together with the mint's ``decimals``.
* Human-readable ("UI") amounts and prices are ``Decimal``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext

from desk.core.errors import UnitError

LAMPORTS_PER_SOL = 1_000_000_000
MAX_DECIMALS = 255  # SPL mints store decimals as u8


def to_decimal(value: object) -> Decimal:
    """Convert provider numbers to Decimal without float rounding (floats go through ``repr``)."""
    if isinstance(value, bool):
        raise UnitError(f"boolean is not a number: {value!r}")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        result = Decimal(repr(value))
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise UnitError(f"not a decimal number: {value!r}") from exc
    else:
        raise UnitError(f"unsupported numeric type {type(value).__name__}: {value!r}")
    if not result.is_finite():
        raise UnitError(f"non-finite number rejected: {value!r}")
    return result


def _check_decimals(decimals: int) -> None:
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= MAX_DECIMALS:
        raise UnitError(f"invalid token decimals: {decimals!r}")


def raw_to_ui(raw: int, decimals: int) -> Decimal:
    """Raw base units -> exact UI amount (e.g. 1_500_000 raw with 6 decimals -> Decimal('1.5'))."""
    _check_decimals(decimals)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise UnitError(f"raw amount must be an int, got {raw!r}")
    with localcontext() as ctx:
        ctx.prec = 400
        return Decimal(raw).scaleb(-decimals)


def ui_to_raw(amount: Decimal | str | int, decimals: int) -> int:
    """Exact UI amount -> raw base units. Raises if the amount has more precision than the mint allows."""
    _check_decimals(decimals)
    value = to_decimal(amount)
    with localcontext() as ctx:
        ctx.prec = 400
        scaled = value.scaleb(decimals)
        if scaled != scaled.to_integral_value():
            raise UnitError(f"{value} has more than {decimals} decimal places")
        return int(scaled)


def lamports_to_sol(lamports: int) -> Decimal:
    return raw_to_ui(lamports, 9)


def sol_to_lamports(sol: Decimal | str | int) -> int:
    return ui_to_raw(sol, 9)
