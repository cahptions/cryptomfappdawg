"""KESTREL's structure pricing (desk.stocks.forecast.structure): exact valuation on known distributions (a
lognormal gives Black–Scholes), monotone quantile functions, a direction deviation that keeps the base shape when there
is no skill, a decomposition that adds up and the card's plain sentence."""

from __future__ import annotations

import math

import numpy as np
import pytest

from desk.stocks import options as opt
from desk.stocks.forecast import direction as dr
from desk.stocks.forecast import structure as st
from desk.stocks.forecast import vol_surprise as vs


# ── structure pricing ────────────────────────────────────────────────────────────────────────────
def test_a_lognormal_prices_calls_puts_and_spreads_like_black_scholes() -> None:
    s, t, r, sigma = 100.0, 0.25, 0.04, 0.3
    f = st.Forecast.lognormal(sigma, r, t)
    for kind in ("call", "put"):
        for k in (80.0, 100.0, 125.0):
            got = st.value(f, s, [st.Leg(kind, k, 1)], r, t)
            assert got == pytest.approx(opt.bs_price(kind, s, k, t, sigma, r), rel=2e-3, abs=2e-3)
    spread = st.value(f, s, [st.Leg("call", 100, 1), st.Leg("call", 110, -1)], r, t)
    assert spread == pytest.approx(opt.bs_price("call", s, 100, t, sigma, r) - opt.bs_price("call", s, 110, t, sigma, r),
                                   abs=2e-3)
    assert f.growth() == pytest.approx(math.exp(r * t), rel=1e-6)


def test_a_chain_table_converts_to_the_same_quantiles() -> None:
    from desk.stocks import chain_odds as co

    d = co.lognormal(0.3, 0.04, 0.25)
    f = st.Forecast.from_table(d.u, d.survival)
    ref = st.Forecast.lognormal(0.3, 0.04, 0.25)
    assert f.quantiles([0.05, 0.5, 0.95]) == pytest.approx(ref.quantiles([0.05, 0.5, 0.95]), abs=2e-3)


def test_full_quantiles_are_monotone_with_straight_tails() -> None:
    q = st.full_quantiles([-2.0, -1.5, -0.6, 0.1, 0.5, 1.0, 1.2])
    assert np.all(np.diff(q) >= -1e-12)
    from scipy.special import ndtri

    x = ndtri(np.asarray(dr.QUANTILES))
    at = np.interp(x, st.Z, q)
    np.testing.assert_allclose(at, [-2.0, -1.5, -0.6, 0.1, 0.5, 1.0, 1.2], atol=5e-3)


def _quantiles(cond: list[float], live: bool = True) -> dr.Quantiles:
    normal = [-1.6449, -1.2816, -0.6745, 0.0, 0.6745, 1.2816, 1.6449]
    return dr.Quantiles(tuple(cond), tuple(normal), live, 0.02, 21)


def test_no_skill_keeps_the_base_shape_and_a_shift_moves_the_median() -> None:
    base = st.Forecast.lognormal(0.3, 0.04, 21 / 252)
    normal = [-1.6449, -1.2816, -0.6745, 0.0, 0.6745, 1.2816, 1.6449]
    same = st.pair(base, t=21 / 252, rate=0.04, drift=0.04, dq=_quantiles(normal), correction=None)
    np.testing.assert_allclose(same.full.q, base.q, atol=1e-6)
    up = st.pair(base, t=21 / 252, rate=0.04, drift=0.04, dq=_quantiles([v + 0.5 for v in normal]), correction=None)
    shift = 0.5 * 0.02 * math.sqrt(21)
    assert up.full.quantiles([0.5])[0] - base.quantiles([0.5])[0] == pytest.approx(shift, abs=1e-3)
    assert up.full.sd() == pytest.approx(base.sd(), rel=1e-3)          # the volatility model alone sets the spread
    off = st.pair(base, t=21 / 252, rate=0.04, drift=0.04, dq=_quantiles([v + 0.5 for v in normal], live=False),
                  correction=None)
    np.testing.assert_allclose(off.full.q, base.q, atol=1e-6)
    wide = st.pair(base, t=21 / 252, rate=0.04, drift=0.04, dq=None, correction=vs.Correction(math.log(1.44), True))
    assert wide.full.sd() == pytest.approx(1.2 * base.sd(), rel=1e-3) and wide.vol_multiplier == pytest.approx(1.2)


def test_the_edge_splits_into_direction_and_volatility_exactly() -> None:
    t, r = 30 / 365, 0.04
    base = st.Forecast.lognormal(0.35, 0.10, t)                # a tilted drift and a higher forecast volatility
    normal = [-1.6449, -1.2816, -0.6745, 0.0, 0.6745, 1.2816, 1.6449]
    p = st.pair(base, t=t, rate=r, drift=0.10, dq=_quantiles([v + 0.3 for v in normal]),
                correction=vs.Correction(0.1, True))
    legs = [st.Leg("call", 100, 1)]
    market = opt.bs_price("call", 100, 100, t, 0.30, r)
    c = st.cost("BUY", market, 1, None, 0.65)
    out = st.price(legs, p, s=100, t=t, rate=r, action="BUY", c=c, risk=c.per_share * 100, net_vega=0.1)
    assert out["direction_part"] + out["vol_part"] == pytest.approx(out["edge"], abs=0.02)
    assert out["vol_part"] > 0 and out["direction_part"] > 0 and out["fill"]["basis"] == "far side"
    assert out["edge_per_risk"] == pytest.approx(out["edge"] / out["cost"], abs=1e-3)
    assert 0 < out["chance_profit"] < 1 and out["range"][0] < 100 < out["range"][2]
    # a sold iron condor: costs a credit, its edge per risk on the maximum loss
    condor = [st.Leg("put", 90, 1), st.Leg("put", 95, -1), st.Leg("call", 105, -1), st.Leg("call", 110, 1)]
    credit = st.cost("SELL", 1.8, 4, None, 0.65)
    assert credit.per_share == pytest.approx(-1.8 + 0.026)
    sold = st.price(condor, p, s=100, t=t, rate=r, action="SELL", c=credit, risk=320.0, net_vega=-0.2)
    assert sold["direction_part"] + sold["vol_part"] == pytest.approx(sold["edge"], abs=0.02)


def test_the_card_sentence_names_the_main_part() -> None:
    assert st.describe(10, 9, 1, 0.1)[0] == "direction"
    driver, text = st.describe(10, 1, 9, 0.1)
    assert driver == "volatility" and text == "Edge mostly from options looking cheap vs how much the stock should move."
    assert "expensive" in st.describe(10, 1, 9, -0.1)[1]
    assert st.describe(10, 5, 5, 0.1)[0] == "both" and st.describe(-1, 5, -6, 0.1)[0] == "none"


def test_the_fill_model_lowers_the_cost() -> None:
    from desk.stocks.learner import fills as fl

    fill = fl.Fill((0.2, 0.5, 0.8, 0.9, 1.0), 0.10, 2)            # half way to the ask, 0.10 of half spreads
    got = st.cost("BUY", 2.0, 1, fill, 0.65)
    assert got.per_share == pytest.approx(2.0 - 0.05 + 0.0065) and got.basis == "fill model" and got.chance == 0.8


def test_rerank_mixes_the_two_ranks() -> None:
    items = ["a", "b", "c", "d"]
    score = {"a": 4, "b": 3, "c": 2, "d": 1}
    edge = {"a": -1.0, "b": 2.0, "c": 0.5, "d": None}
    assert st.rerank(items, score.__getitem__, edge.__getitem__) == ["b", "a", "c", "d"]


