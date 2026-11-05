"""Option prices read the stock's trailing-12-month dividend yield: in the replay's model, its calibration and fills."""

import math
from datetime import date, timedelta

import numpy as np
import pytest

from desk.stocks import calendar as cal
from desk.stocks import option_model as om
from desk.stocks import options as opt
from desk.stocks.contracts import Dividend, Split
from desk.stocks.paper import fills
from desk.stocks.prices import Panel

DAY = date(2026, 6, 1)


def quarterly(symbol: str = "AAA", amount: float = 0.5) -> list[Dividend]:
    return [Dividend(symbol, DAY - timedelta(days=d), amount, None, 4, "CD") for d in (20, 110, 200, 290, 380)]


def test_trailing_yield_counts_the_last_year_known_by_the_day_and_adjusts_for_later_splits():
    later = Dividend("AAA", DAY + timedelta(days=5), 9.0, None, 4, "CD")          # not yet ex on DAY
    d = fills.Dividends([*quarterly(), later], [])
    assert d.per_share("AAA", DAY) == pytest.approx(2.0) and d.yield_on("AAA", DAY, 100.0) == pytest.approx(0.02)
    assert d.yield_on("BBB", DAY, 100.0) == 0.0 and d.yield_on("AAA", DAY, None) == 0.0
    split = fills.Dividends(quarterly(), [Split("AAA", DAY - timedelta(days=60), 1, 2)])
    assert split.per_share("AAA", DAY) == pytest.approx(0.5 + 1.5 / 2)            # older ones per new share
    assert split.per_share("AAA", DAY - timedelta(days=90)) == pytest.approx(2.0)   # before the split, old shares
    assert split.per_share("AAA", DAY - timedelta(days=90), basis=DAY) == pytest.approx(1.0)
    assert fills.Dividends([Dividend("AAA", DAY, 80.0, None, 0, "SC")], []).yield_on("AAA", DAY, 100.0) == 0.25


def test_modeled_chain_and_fills_price_calls_lower_and_puts_higher_with_a_dividend():
    days = cal.trading_days(DAY - timedelta(days=200), DAY)[-80:]
    closes = np.array([[100 * math.exp(0.015 * (1 if i % 2 else -1)) for i in range(80)]])
    panel = Panel(["AAA"], days, closes, closes, closes, closes, np.ones_like(closes) * 1e6)
    calibration = om.Calibration({}, 1.1, 0.1, {}, 0.1)
    plain = om.ModeledOptions(calibration, om.RealBars(), {"AAA"})
    paying = om.ModeledOptions(calibration, om.RealBars(), {"AAA"}, fills.Dividends(quarterly(amount=1.0), []))
    for m in (plain, paying):
        m.session(DAY, panel, 0.04)
    a, b = fills.index_chain(plain.chain("AAA")), fills.index_chain(paying.chain("AAA"))
    call = next(c for c in a if c.endswith("C00100000"))
    put = call[:-9] + "P00100000"
    assert b[call].ask < a[call].ask and b[put].ask > a[put].ask
    leg = {"kind": "call", "strike": 100.0, "expiration": "2026-07-17", "last_iv": 0.3, "quantity": 1, "occ": call}
    assert fills.model_price(leg, 100.0, DAY, 0.04, 0.04) < fills.model_price(leg, 100.0, DAY, 0.04)
    value, _, modelled = fills.mark_legs([leg], {}, 100.0, DAY, 0.04, 0.04)
    assert modelled and value == pytest.approx(100 * fills.model_price(leg, 100.0, DAY, 0.04, 0.04))
    t = opt.year_fraction(DAY, date(2026, 7, 17))
    price = opt.bs_price("call", 100.0, 100.0, t, 0.3, 0.04, 0.04)
    q = fills.index_chain({date(2026, 7, 17): []})
    assert fills.close_legs([leg], q, 100.0, DAY, 0.04, 0.04).debit == pytest.approx(-price * 0.95 * 100)


def test_calibration_reads_massive_prices_with_the_dividend_yield(tmp_path):
    from desk.stocks.prices import load_panel
    from tests.stocks_world import make_world

    w = make_world(tmp_path, n=8)
    try:
        p = load_panel(w.market, w.days[0])
        symbol, i = "PRICEY", p.index["PRICEY"]          # no collected chain summaries to mix in
        first = w.days[-40]
        paid = float(p.close[i, p.day_index(first)]) * 0.03
        dividends = fills.Dividends([Dividend(symbol, first - timedelta(days=10), paid, None, 1, "CD")], [])
        rows = []
        for day in w.days[-40:-28]:
            s = float(p.close[i, p.day_index(day)])
            expiration = om.monthly_expiries(day + timedelta(days=30))[0]
            k = float(round(s))
            dy = dividends.yield_on(symbol, day, s)
            rows.append((om.occ(symbol, expiration, "call", k), day.toordinal(),
                         opt.bs_price("call", s, k, opt.year_fraction(day, expiration), 0.5, 0.04, dy)))
        real = om.RealBars(rows)
        with_q = om.calibrate(w.market, p, real, 0.04, dividends=dividends)
        without = om.calibrate(w.market, p, real, 0.04)
        assert with_q.observations["massive_prices"] == without.observations["massive_prices"] == 12
        # the same prices imply more volatility once the dividend they carry is priced in
        assert with_q.ratios[symbol] > without.ratios[symbol]
    finally:
        w.close()
