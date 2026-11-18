"""Skill beyond the market's direction (desk.stocks.learner.neutral): same-horizon market and sector returns from stored
closes only, and neutralising removes a pure market-beta effect while keeping real skill. Synthetic data only."""

import sqlite3

import numpy as np
from scipy.stats import spearmanr

from desk.stocks.learner import features as ft
from desk.stocks.learner import neutral as nt


def test_horizon_returns_come_from_stored_closes_only() -> None:
    market = sqlite3.connect(":memory:")
    market.execute("CREATE TABLE stk_bars (symbol TEXT, day INTEGER, close REAL)")
    market.executemany("INSERT INTO stk_bars VALUES (?,?,?)", [("SPY", 100, 400.0), ("SPY", 110, 420.0),
                                                               ("XLK", 100, 200.0), ("XLK", 110, 190.0)])
    tech = float(ft.SECTORS.index("BusEq"))
    mkt, sec = nt.horizon_returns(market, np.array([100, 100, 100, 100]), np.array([110, 105, 0, 110]),
                                  np.array([tech, tech, tech, np.nan], dtype=np.float32))
    assert mkt[0] == np.float32(0.05) and sec[0] == np.float32(-0.05)
    assert np.isnan(mkt[1]) and np.isnan(sec[1])                  # no stored close on the label day: missing
    assert np.isnan(mkt[2])                                        # no label yet: missing
    assert mkt[3] == np.float32(0.05) and np.isnan(sec[3])         # no sector: only the market
    none = nt.horizon_returns(None, np.array([100]), np.array([110]), np.array([tech]))
    assert np.isnan(none[0]).all() and np.isnan(none[1]).all()


def test_neutralising_removes_a_pure_market_beta_effect() -> None:
    rng = np.random.default_rng(3)
    n = 6000
    structure = rng.integers(0, 2, n).astype(np.float32)
    beta = np.where(structure == 0, 3.0, -2.0)                     # a call-like and a put-like structure
    mkt = rng.normal(0, 0.05, n).astype(np.float32)
    sec = (mkt + rng.normal(0, 0.02, n)).astype(np.float32)
    skill = rng.normal(0, 0.1, n)
    y = (0.02 + beta * mkt + 1.5 * (sec - mkt) + skill + rng.normal(0, 0.1, n)).astype(np.float32)
    train = rng.random(n) < 0.5
    b = nt.betas(y, mkt, sec, structure, train)
    assert abs(b[0][1] - 3.0) < 0.3 and abs(b[1][1] + 2.0) < 0.3 and abs(b[0][2] - 1.5) < 0.5
    neutral = nt.neutralise(y, mkt, sec, structure, b)
    test = ~train
    timing = beta * mkt                                            # a "skill" that is only the market's direction
    raw_ic = spearmanr(timing[test], y[test]).statistic
    neutral_ic = spearmanr(timing[test], neutral[test]).statistic
    assert raw_ic > 0.5 and abs(neutral_ic) < 0.05
    assert abs(np.corrcoef(mkt[test], neutral[test])[0, 1]) < 0.05
    assert spearmanr(skill[test], neutral[test]).statistic > spearmanr(skill[test], y[test]).statistic
    assert abs(float(np.mean(neutral)) - float(np.mean(y - beta * mkt - 1.5 * (sec - mkt)))) < 0.01   # the level stays


def test_rows_without_the_market_return_have_no_neutral_return() -> None:
    y = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    out = nt.neutralise(y, np.array([0.01, np.nan, 0.02], dtype=np.float32), np.full(3, np.nan, np.float32),
                        np.zeros(3, np.float32), {0: (0.0, 2.0, 0.0)})
    assert np.isnan(out[1]) and np.allclose(out[[0, 2]], [0.1 - 0.02, 0.3 - 0.04])
    assert nt.betas(y, np.full(3, 0.01, np.float32), np.full(3, np.nan, np.float32), np.zeros(3, np.float32),
                    np.ones(3, bool)) == {}                        # too few rows for a structure's own betas
