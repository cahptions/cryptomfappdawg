"""M1-08: exact unit conversions."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from desk.core.errors import UnitError
from desk.core.units import lamports_to_sol, raw_to_ui, sol_to_lamports, to_decimal, ui_to_raw


@given(raw=st.integers(min_value=0, max_value=2**64 - 1), decimals=st.integers(min_value=0, max_value=12))
def test_raw_ui_round_trip_is_exact(raw: int, decimals: int) -> None:
    assert ui_to_raw(raw_to_ui(raw, decimals), decimals) == raw


@given(lamports=st.integers(min_value=0, max_value=10**18))
def test_lamports_round_trip(lamports: int) -> None:
    assert sol_to_lamports(lamports_to_sol(lamports)) == lamports


def test_known_values() -> None:
    assert raw_to_ui(1_500_000, 6) == Decimal("1.5")
    assert raw_to_ui(1_000_000_000_000_000, 6) == Decimal("1000000000")  # pump.fun supply from the M0 fixture
    assert lamports_to_sol(1_488_440) == Decimal("0.00148844")           # measured rent for 165 B
    assert sol_to_lamports("0.1") == 100_000_000


def test_excess_precision_rejected() -> None:
    with pytest.raises(UnitError):
        ui_to_raw("0.0000001", 6)
    with pytest.raises(UnitError):
        sol_to_lamports("0.0000000001")


@pytest.mark.parametrize("bad", [-1, 256, True, 1.5])
def test_invalid_decimals_rejected(bad: object) -> None:
    with pytest.raises(UnitError):
        raw_to_ui(1, bad)  # type: ignore[arg-type]


def test_high_precision_provider_string_parses_without_float_loss() -> None:
    text = "0.00000675256853368184059069883123401624669282330265106450674269909548"  # from GT fixture
    assert str(to_decimal(text)) == text


def test_float_goes_through_repr_not_binary_expansion() -> None:
    assert to_decimal(0.1) == Decimal("0.1")


@pytest.mark.parametrize("bad", ["NaN", "inf", "abc", True, None, [1]])
def test_non_numbers_rejected(bad: object) -> None:
    with pytest.raises(UnitError):
        to_decimal(bad)
