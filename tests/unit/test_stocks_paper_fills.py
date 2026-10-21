"""PAPER fills for KESTREL Stocks never flatter an account: stocks pay slippage and sale fees, stops gap, options buy
at the ask and sell at the bid, unfair quotes refuse to fill, and expiry pays intrinsic value."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from desk.stocks.contracts import Bar, OptionQuote
from desk.stocks.paper import fills
from desk.stocks.paper.book import to_micros

D = date(2026, 10, 21)


def bar(o: float, h: float, lo: float, c: float) -> Bar:
    return Bar("X", D, o, h, lo, c, 1e6)


def quote(occ: str, bid: float | None, ask: float | None, kind: str = "call", strike: float = 100.0) -> OptionQuote:
    return OptionQuote(occ, "X", kind, strike, date(2026, 11, 20), bid, ask, None, 100, 1000, 100,
                       datetime(2026, 10, 21, 21, tzinfo=UTC))


def test_money_rounds_against_the_account() -> None:
    assert to_micros(1.0000001, cost=True) == 1_000_001
    assert to_micros(1.0000009, cost=False) == 1_000_000
    assert to_micros(2.5, cost=True) == to_micros(2.5, cost=False) == 2_500_000


def test_stock_fills_pay_slippage_and_sale_fees() -> None:
    f = fills.buy_at_open(bar(100, 101, 99, 100.5), 1000.0, 80_000_000)
    assert f is not None and f.price == pytest.approx(100.05) and f.shares == pytest.approx(9.995)
    assert f.cost <= 1000.0
    thin = fills.buy_at_open(bar(100, 101, 99, 100.5), 1000.0, 2_000_000)
    assert thin is not None and thin.price == pytest.approx(100.15)
    s = fills.sell_at(100.0, 10, 80_000_000, "open")
    assert s.price == pytest.approx(99.95) and s.fees == pytest.approx(round(999.5 * fills.SEC_FEE_RATE + 10 * 0.000166, 2))
    assert fills.sale_fees(1_000_000, 1e8) == pytest.approx(round(1e8 * fills.SEC_FEE_RATE + 8.30, 2))   # TAF capped
    assert fills.buy_at_open(bar(5000, 5000, 5000, 5000), 0.1, None) is None


def test_stops_fill_at_the_stop_or_the_gap() -> None:
    assert fills.stop_hit(bar(100, 101, 95, 99), 96.0) == 96.0
    assert fills.stop_hit(bar(90, 92, 88, 91), 96.0) == 90.0             # gapped through: sold at the open
    assert fills.stop_hit(bar(100, 101, 97, 99), 96.0) is None
    assert fills.stop_hit(bar(100, 101, 97, 99), None) is None


def test_options_buy_at_the_ask_sell_at_the_bid_and_refuse_unfair_quotes() -> None:
    quotes = {"C1": quote("C1", 2.00, 2.10), "C2": quote("C2", 0.90, 1.00, strike=110.0),
              "WIDE": quote("WIDE", 0.50, 1.50), "NOBID": quote("NOBID", 0.0, 0.10)}
    legs = [{"occ": "C1", "kind": "call", "strike": 100.0, "expiration": "2026-11-20", "quantity": 2, "decision_price": 2.05},
            {"occ": "C2", "kind": "call", "strike": 110.0, "expiration": "2026-11-20", "quantity": -2}]
    f = fills.open_legs(legs, quotes)
    assert f.problem is None
    assert f.debit == pytest.approx(2 * 2.10 * 100 - 2 * 0.90 * 100) and f.fees == pytest.approx(4 * 0.65)
    assert [leg["entry_price"] for leg in f.legs] == [2.10, 0.90]
    assert "spread too wide" in (fills.open_legs([{**legs[0], "occ": "WIDE"}], quotes).problem or "")
    assert "spread too wide" in (fills.open_legs([{**legs[0], "occ": "NOBID"}], quotes).problem or "")
    assert "not in the chain" in (fills.open_legs([{**legs[0], "occ": "GONE"}], quotes).problem or "")
    assert "ran past" in (fills.open_legs([{**legs[0], "decision_price": 1.80}], quotes).problem or "")
    cheap = {"C3": quote("C3", 0.12, 0.14)}
    assert fills.open_legs([{**legs[0], "occ": "C3", "decision_price": 0.10}], cheap).problem is None   # within 5 cents
    assert fills.limit(2.0) == pytest.approx(2.2) and fills.limit(0.2) == pytest.approx(0.25)
    out = fills.close_legs(f.legs, quotes, 104.0, D, 0.04)
    assert out.debit == pytest.approx(-(2 * 2.00 * 100 - 2 * 1.00 * 100))


def test_marks_use_mids_then_the_model_and_expiry_pays_intrinsic() -> None:
    legs = [{"occ": "C1", "kind": "call", "strike": 100.0, "expiration": "2026-11-20", "quantity": 1, "entry_iv": 0.4}]
    value, marked, modelled = fills.mark_legs(legs, {"C1": quote("C1", 2.00, 2.20)}, 98.0, D, 0.04)
    assert value == pytest.approx(210.0) and not modelled and marked[0]["last_iv"] > 0
    value, _, modelled = fills.mark_legs(legs, {}, 98.0, D, 0.04)
    assert modelled and value is not None and 0 < value < 600
    closing = fills.close_legs(legs, {}, 98.0, D, 0.04)
    assert closing.problem is None and -closing.debit < value             # a modelled exit takes a haircut
    spread = [legs[0], {**legs[0], "occ": "C2", "strike": 110.0, "quantity": -1}]
    assert fills.settle(spread, 104.0) == pytest.approx(400.0)
    assert fills.settle(spread, 125.0) == pytest.approx(1000.0)            # capped at the width
    assert fills.settle(spread, 90.0) == 0.0
    put = [{"occ": "P", "kind": "put", "strike": 100.0, "expiration": "2026-11-20", "quantity": 3}]
    assert fills.settle(put, 97.5) == pytest.approx(750.0)
