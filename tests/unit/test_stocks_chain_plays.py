"""Plays priced on the chain's own distribution: each expiry says its basis, a fitted expiry's chances, expected value,
targets and plan Monte Carlo all come from the same distribution, and a sparse expiry keeps today's lognormal."""

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from desk.stocks import chain_odds as co
from desk.stocks import odds as od
from desk.stocks import play_plan as pp
from desk.stocks import plays as pl
from desk.stocks.contracts import OptionQuote
from tests.stocks_world import occ
from tests.unit.test_stocks_chain_odds import mixture

AT = datetime(2026, 10, 28, 15, tzinfo=UTC)
SPOT, RATE = 100.0, 0.04
SKEW = [(0.8, 1.02, 0.20), (0.2, 0.92, 0.45)]


def context() -> pl.Context:
    return pl.Context(AT.date(), AT, RATE, None, 0, None, {"itm": od.Calibration(), "move": od.Calibration()}, [])


def quotes(days: int, multiples: list[float]) -> list[OptionQuote]:
    t = days / 365
    call, _ = mixture(t, SKEW)
    expiry = AT.date() + timedelta(days=days)
    out = []
    for m in multiples:
        k = round(SPOT * m, 2)
        c = float(call(np.array([k]))[0])
        for kind, mid in (("call", c), ("put", c - SPOT + k * math.exp(-RATE * t))):
            if mid < 0.08:
                continue
            half = max(0.01, 0.02 * mid)
            out.append(OptionQuote(occ("SKEW", expiry, kind, k), "SKEW", kind, k, expiry, round(mid - half, 4),
                                   round(mid + half, 4), mid, 1000, 5000, None, AT))
    return out


def skew_table() -> pl.Table:
    dense = [round(0.70 + 0.025 * i, 3) for i in range(27)]
    chains = {AT.date() + timedelta(days=30): quotes(30, dense),
              AT.date() + timedelta(days=45): quotes(45, [0.97, 1.0, 1.03])}         # too sparse to fit
    u = pl.Underlying("SKEW", SPOT, AT.isoformat(), "synthetic", chains,
                      detail={"committee": {"score": 65, "label": "Bullish lean"}}, committee=65, rv=0.22, iv30=0.25)
    tb = pl.table(u, context())
    assert tb is not None
    return tb


def candidate(tb: pl.Table, days: int, kind: str, strike: float) -> pl.Candidate:
    i = next(j for j in range(len(tb)) if tb.dte[j] == days and bool(tb.call[j]) == (kind == "call")
             and tb.k[j] == strike)
    return pl.Candidate("likely_itm", tb, f"long_{kind}", [(i, "buy")], 1.0, ["test"])


def test_each_expiry_says_its_basis_and_the_table_prices_on_it() -> None:
    tb = skew_table()
    fitted, sparse = tb.fits[AT.date() + timedelta(days=30)], tb.fits[AT.date() + timedelta(days=45)]
    assert fitted.basis == "chain" and fitted.market is not None and fitted.kestrel is not None
    assert sparse.basis == "lognormal" and "usable strikes" in sparse.reason
    rows = np.where(tb.dte == 30)[0]
    i = next(j for j in rows if not tb.call[j] and tb.k[j] == 85.0)
    assert tb.itm_market[i] == pytest.approx(1 - float(fitted.market.above(SPOT, 85.0)))
    assert tb.payoff[i] == pytest.approx(float(fitted.kestrel.expected_payoff(False, SPOT, 85.0)))
    assert tb.sigma[i] == pytest.approx(fitted.sigma) and tb.mu[i] == pytest.approx(fitted.drift)
    # nearer the true chance of finishing below the far put strike than the lognormal at its own volatility (which
    # overstates it) or at the money's (which leaves out the crash)
    t = float(tb.t[i])
    truth = 1 - float(mixture(t, SKEW)[1](np.array([85.0]))[0])
    own, atm = 1 - od.above(SPOT, 85.0, t, float(tb.iv[i]), RATE), 1 - od.above(SPOT, 85.0, t, fitted.atm_iv or 0, RATE)
    assert atm < truth < own
    assert abs(tb.itm_market[i] - truth) < 0.1 * min(abs(own - truth), abs(atm - truth))


def test_a_fitted_play_takes_every_chance_from_the_chains_distribution() -> None:
    tb = skew_table()
    fit = tb.fits[AT.date() + timedelta(days=30)]
    assert fit.kestrel is not None and fit.market is not None
    play = pl.finish(candidate(tb, 30, "put", 85.0), context())
    assert play["odds_basis"]["basis"] == "chain" and play["odds_basis"]["strikes"] >= 5
    assert play["itm_market"] == pytest.approx(1 - float(fit.market.above(SPOT, 85.0)), abs=1e-4)
    assert play["itm_kestrel_raw"] == pytest.approx(1 - float(fit.kestrel.above(SPOT, 85.0)), abs=1e-4)
    assert play["profit_kestrel"] == pytest.approx(1 - float(fit.kestrel.above(SPOT, play["breakeven"])), abs=1e-3)
    assert play["forecast"]["basis"] == "chain" and len(play["forecast"]["quantiles"]) == len(co.QUANTILES)
    profit = play["probability_targets"]["returns"]["0"]
    assert profit == pytest.approx(play["profit_kestrel"], abs=0.01)      # fees aside, the same event


def test_a_sparse_expiry_keeps_todays_lognormal() -> None:
    tb = skew_table()
    play = pl.finish(candidate(tb, 45, "call", 103.0), context())
    assert play["odds_basis"]["basis"] == "lognormal" and "usable strikes" in play["odds_basis"]["reason"]
    leg = play["legs"][0]
    t = 45 / 365
    want = od.odds(kind="call", action="buy", s=SPOT, k=103.0, t=t, iv=leg["iv"], rv=0.22, r=RATE,
                   premium=play["entry"], history=None, ic=None, ic_months=0, z=None)
    assert play["itm_market"] == pytest.approx(want.itm_market, abs=1e-4)
    assert play["itm_kestrel_raw"] == pytest.approx(want.itm_kestrel_raw, abs=1e-4)
    assert play["forecast"] == {"sigma": round(want.sigma, 4), "drift": round(want.drift, 4), "basis": "lognormal"}


def test_the_plan_monte_carlo_finishes_on_the_same_distribution() -> None:
    tb = skew_table()
    fit = tb.fits[AT.date() + timedelta(days=30)]
    assert fit.kestrel is not None and fit.sigma is not None
    q = fit.kestrel.quantiles()
    rng = np.random.default_rng(7)
    days = np.linspace(1, 30, 30)
    paths = pp._bridged(q, fit.sigma, days, 30, rng.standard_normal((20_000, 30)), rng)
    for level in (85.0, 100.0, 110.0):          # at expiry: exactly the stored distribution
        assert float(np.mean(SPOT * np.exp(paths[:, -1]) > level)) == pytest.approx(
            float(fit.kestrel.above(SPOT, level)), abs=0.012)
    halfway = paths[:, 14]                       # on the way: a Brownian bridge, spread about σ·√t
    assert float(np.std(halfway)) == pytest.approx(
        math.sqrt(fit.sigma ** 2 * 15 / 365 * (1 - 15 / 30) + (15 / 30) ** 2 * float(np.var(paths[:, -1]))), rel=0.05)
    play = pl.finish(candidate(tb, 30, "put", 95.0), context())
    plan = pp.exit_plan(play, "likely_itm", None, RATE, AT.date())
    odds = pp.plan_odds(play, plan, RATE, AT.date())
    assert odds is not None and 0.0 <= odds["take_profit"] <= 1.0
    assert odds == pp.plan_odds(play, plan, RATE, AT.date())                 # seeded: the same number every build
