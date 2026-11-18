"""KESTREL's learned volatility forecast (desk.stocks.vol_forecast): realized variance from minute bars with its
fallback, a walk-forward that never sees the future, a gate that keeps the old blend unless the learned forecast beat
it out of sample on enough data, and replays that use only fits made before their day."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pytest

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks import minute_archive as ma
from desk.stocks import vol_forecast as vf
from desk.stocks.contracts import IntradayBar
from desk.stocks.repo import StocksDatabases
from tests.conftest import REPO

PLANT = (-0.8, 0.25, 0.2, 0.2, 0.25)          # intercept, log RV day, week, month, log IV²


def planted(stocks: int = 40, sessions: int = 700, seed: int = 1, *, iv_missing: float = 0.0) -> vf.Panel:
    """A log-HAR-IV process: tomorrow's log variance is exactly ``PLANT`` · features + noise."""
    rng = np.random.default_rng(seed)
    days = cal.trading_days(date(2023, 1, 3), date(2026, 12, 31))[:sessions]
    lv, z = np.full((stocks, sessions), -8.0), np.full((stocks, sessions), -8.0)
    v = np.exp(lv)
    c, bd, bw, bm, bi = PLANT
    for t in range(sessions - 1):
        z[:, t + 1] = -8 + 0.95 * (z[:, t] + 8) + 0.1 * rng.standard_normal(stocks)
        if t >= 22:
            w, m = np.log(v[:, t - 4:t + 1].mean(1)), np.log(v[:, t - 21:t + 1].mean(1))
            lv[:, t + 1] = c + bd * np.log(v[:, t]) + bw * w + bm * m + bi * z[:, t] + 0.4 * rng.standard_normal(stocks)
        else:
            lv[:, t + 1] = -8 + 0.4 * rng.standard_normal(stocks)
        v[:, t + 1] = np.exp(lv[:, t + 1])
    iv = np.exp(z)
    if iv_missing:
        iv[rng.random(iv.shape) < iv_missing] = np.nan
    return vf.Panel([f"S{i:02d}" for i in range(stocks)], days, v, np.sqrt(v) * rng.standard_normal(v.shape), iv)


def blend_is_truth(stocks: int = 40, sessions: int = 500, seed: int = 2) -> vf.Panel:
    """Tomorrow's variance is exactly the old blend of today's implied and 20-session realized variance."""
    rng = np.random.default_rng(seed)
    days = cal.trading_days(date(2023, 1, 3), date(2026, 12, 31))[:sessions]
    v, ret = np.full((stocks, sessions), math.exp(-8)), np.zeros((stocks, sessions))
    iv = np.exp(-8 + 0.8 * rng.standard_normal((stocks, sessions)))
    for t in range(sessions):
        if t > 21:
            window = ret[:, t - 20:t]
            rv20 = (window ** 2).mean(1) - window.mean(1) ** 2
            v[:, t] = vf.IV_WEIGHT * iv[:, t - 1] + (1 - vf.IV_WEIGHT) * rv20
        ret[:, t] = np.sqrt(v[:, t]) * rng.standard_normal(stocks)
    # the old blend at t uses returns t-19..t; shift so the truth at t+1 is the blend formed at t
    return vf.Panel([f"S{i:02d}" for i in range(stocks)], days, v, ret, iv)


def test_a_planted_har_process_is_recovered_and_goes_live() -> None:
    fits = vf.walk_forward(planted())
    newest = fits[-1].model
    beta = newest.coef["har_iv"][1].beta
    assert beta[1:] == pytest.approx(PLANT[1:], abs=0.05)
    assert beta[0] == pytest.approx(PLANT[0], abs=0.5)
    assert all(newest.live.values()) and newest.methods() == {str(h): "har_iv" for h in vf.HORIZONS}
    e = fits[-1].evaluation["1"]
    assert e["qlike"]["har_iv"] < e["qlike"]["blend"] and e["mse_log"]["har_iv"] < e["mse_log"]["blend"]
    assert e["qlike"]["har_iv"] < e["qlike"]["har_rv"] < e["qlike"]["iv"]
    assert e["reduction_qlike"] > 0.1 and e["stocks"] == 40 and e["sessions"] >= vf.GATE_SESSIONS
    s = vf.State(date(2026, 1, 5), math.exp(-8), math.exp(-8), math.exp(-8), math.exp(-7.5))
    sigma = vf.forecast_sigma(0.3, 0.2, t=10 / 252, vol=vf.Vol(s, newest))
    assert sigma.method == "har_iv" and sigma.sigma is not None and 0.05 < sigma.sigma < 1.0


def test_the_gate_keeps_the_old_blend_where_it_was_not_beaten() -> None:
    fits = vf.walk_forward(blend_is_truth())
    newest = fits[-1]
    e = newest.evaluation["1"]
    assert e["qlike"]["blend"] < e["qlike"]["har_iv"] and not e["live"]
    assert not newest.model.live[1]
    s = vf.State(newest.day, math.exp(-8), math.exp(-8), math.exp(-8))
    sigma = vf.forecast_sigma(0.3, 0.2, t=1 / 252, vol=vf.Vol(s, newest.model))
    assert sigma == (vf.blend(0.3, 0.2), "blend")
    # between fitted horizons both neighbours must be live: 3 sessions sits between 1 (not live) and 5
    assert vf.forecast_sigma(0.3, 0.2, t=3 / 252, vol=vf.Vol(s, newest.model)).method == "blend"


def test_too_few_stocks_never_go_live_even_when_the_learned_forecast_wins() -> None:
    fits = vf.walk_forward(planted(stocks=12))
    e = fits[-1].evaluation["1"]
    assert e["beats"] and not e["enough"] and not any(fits[-1].model.live.values())


def test_a_planted_future_shock_cannot_change_earlier_fits_or_their_gate() -> None:
    panel = planted(stocks=35, sessions=520)
    before = vf.walk_forward(panel)
    k = 400
    shocked = replace(panel, rv=panel.rv.copy(), ret=panel.ret.copy(), iv=panel.iv.copy())
    shocked.rv[:, k:] *= 25
    shocked.ret[:, k:] *= 5
    shocked.iv[:, k:] *= 25
    after = vf.walk_forward(shocked)
    cut = panel.days[k]
    early = [(f.day, f.model.sha, f.evaluation) for f in before if f.day <= cut]
    assert len(early) > 20
    assert early == [(f.day, f.model.sha, f.evaluation) for f in after if f.day <= cut]
    assert [f.model.sha for f in before if f.day > cut] != [f.model.sha for f in after if f.day > cut]


def test_missing_implied_volatility_uses_the_model_without_it() -> None:
    fits = vf.walk_forward(planted(iv_missing=0.3))
    newest = fits[-1].model
    assert newest.coef["har_rv"][5].rows > newest.coef["har_iv"][5].rows
    s = vf.State(newest.fit_day, math.exp(-8), math.exp(-8), math.exp(-8))
    assert newest.live_no_iv[5]
    got = vf.forecast_sigma(None, 0.2, t=5 / 252, vol=vf.Vol(s, newest))
    assert got.method == "har_rv" and got.sigma is not None
    assert vf.forecast_sigma(None, 0.2, t=5 / 252) == (0.2, "blend")     # no fit: realized alone, as before


def test_between_and_beyond_the_fitted_horizons() -> None:
    newest = vf.walk_forward(planted())[-1].model
    s = vf.State(newest.fit_day, math.exp(-7), math.exp(-7.5), math.exp(-8), long=math.exp(-9))
    iv = np.array([0.3])
    v5, v7, v10 = (float(newest.variances(s, iv, h)[0][0]) for h in (5, 7, 10))     # type: ignore[index]
    assert min(v5, v10) <= v7 <= max(v5, v10)
    v63, v126 = (float(newest.variances(s, iv, h)[0][0]) for h in (63, 126))       # type: ignore[index]
    assert v126 == pytest.approx((63 * v63 + 63 * math.exp(-9)) / 126)


# ── the stored table, and replays ────────────────────────────────────────────────────────────────
@pytest.fixture
def dbs(tmp_path: Path):  # type: ignore[no-untyped-def]
    out = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    yield out
    out.close()


def _bars(conn, symbol: str, rows: list[tuple[date, float, float]]) -> None:  # type: ignore[no-untyped-def]
    conn.executemany("INSERT INTO stk_bars VALUES (?,?,?,?,?,?,?,?,?)",
                     [(symbol, d.toordinal(), o, max(o, c), min(o, c), c, 1e6, None, None) for d, o, c in rows])


def test_realized_variance_from_five_minute_bars_with_the_daily_fallback(dbs: StocksDatabases) -> None:
    conn = dbs.market
    days = cal.trading_days(date(2026, 9, 1), date(2026, 10, 30))
    split_day = days[20]
    rows, close = [], 100.0
    for i, d in enumerate(days):
        if d == split_day:
            close /= 2                                         # a 2-for-1 split: raw prices halve
        new = close * math.exp(0.01 * (-1) ** i)
        rows.append((d, close * math.exp(0.002), new))
        close = new
    minute_day, short_day = days[30], days[31]
    p0 = rows[30][1]
    rows[30] = (minute_day, p0, p0 * math.exp(0.002))         # its close matches the last minute's
    _bars(conn, "AAA", rows)
    conn.execute("INSERT INTO stk_splits VALUES ('AAA', ?, 1, 2, '2026-09-01T00:00:00Z')", (split_day.toordinal(),))
    opened = datetime.combine(minute_day, time(9, 30), cal.NEW_YORK)
    prices = [p0 * math.exp(0.002 * ((k // 5) % 2)) for k in range(390)]
    bars = [IntradayBar("AAA", opened + timedelta(minutes=k), p, p, p, p, 100.0) for k, p in enumerate(prices)]
    short_open = datetime.combine(short_day, time(9, 30), cal.NEW_YORK)
    bars += [IntradayBar("AAA", short_open + timedelta(minutes=k), 50.0, 50.0, 50.0, 50.0, 1.0) for k in range(100)]
    now = cal.session_close(days[-1]) + timedelta(hours=2)
    ma.archive(conn, bars, now)
    out = vf.update(conn, now, symbols=["AAA"])
    assert out["remaining"] == 0 and out["rows_written"] == len(days) - 1
    got = {r[0]: (r[1], r[2], r[3], r[4]) for r in conn.execute("SELECT day, rv, ret, source, minutes FROM stk_rv_daily")}
    rv, _, source, minutes = got[minute_day.toordinal()]
    overnight = math.log(p0 / rows[29][2])
    assert source == "minutes5" and minutes == 390
    assert rv == pytest.approx(overnight ** 2 + 77 * 0.002 ** 2, rel=1e-4)     # minutes are kept to 4 decimals
    rv, ret, source, minutes = got[short_day.toordinal()]
    assert source == "daily" and minutes is None and rv == pytest.approx(ret ** 2)
    assert abs(got[split_day.toordinal()][1]) < 0.05                # no fake −69% on the split day
    assert vf.update(conn, now, symbols=["AAA"])["rows_written"] == 0  # computed once
    s = vf.state(conn, "AAA", days[-1])
    assert s is not None and s.day == days[-1]
    assert vf.state(conn, "AAA", days[-1] + timedelta(days=30)) is None   # stale rows are no state


def test_replays_use_only_fits_made_on_or_before_their_day(dbs: StocksDatabases) -> None:
    conn = dbs.market
    fits = vf.walk_forward(planted())
    now = datetime(2026, 11, 18, 22, tzinfo=UTC)
    assert vf.save_fits(conn, fits, now) == len(fits)
    assert vf.save_fits(conn, fits, now) == 0                              # a stored fit day is never rewritten
    first, middle = fits[0].day, fits[len(fits) // 2]
    assert vf.model_on(conn, first - timedelta(days=1)) is None
    look = vf.Lookup(conn)
    assert look.model(first - timedelta(days=1)) is None
    for d in (middle.day, middle.day + timedelta(days=3)):
        m = vf.model_on(conn, d)
        assert m is not None and m.fit_day == middle.day and m.sha == middle.model.sha
        assert look.model(d) == m
    s = vf.State(first, math.exp(-8), math.exp(-8), math.exp(-8))
    assert vf.forecast_sigma(0.3, 0.2, t=0.1, vol=vf.Vol(s, look.model(first - timedelta(days=1)))).method == "blend"
    info = vf.latest(conn)
    assert info is not None and info["fit_day"] == fits[-1].day.isoformat() and info["methods"]["1"] == "har_iv"


def test_the_chances_and_replays_use_the_forecast_only_where_a_past_fit_is_live(dbs: StocksDatabases) -> None:
    from desk.stocks import lotto_history, play_replay
    from desk.stocks import odds as od

    conn = dbs.market
    fits = vf.walk_forward(planted())
    vf.save_fits(conn, fits, datetime(2026, 11, 18, 22, tzinfo=UTC))
    s = vf.State(fits[-1].day, math.exp(-7), math.exp(-7), math.exp(-7))
    kw = {"kind": "call", "action": "buy", "s": 100.0, "k": 105.0, "t": 30 / 365, "iv": 0.3, "rv": 0.25, "r": 0.04,
          "premium": 1.0, "history": None, "ic": None, "ic_months": 0.0, "z": None}
    old = od.odds(**kw)                                                     # type: ignore[arg-type]
    assert old.sigma == pytest.approx(vf.blend(0.3, 0.25))
    learned = od.odds(**kw, vol=vf.Vol(s, fits[-1].model))                 # type: ignore[arg-type]
    assert learned.sigma == pytest.approx(vf.forecast_sigma(0.3, 0.25, t=30 / 365, vol=vf.Vol(s, fits[-1].model)).sigma)
    assert learned.sigma != pytest.approx(old.sigma)
    # a replay day before the first fit: the old blend, exactly as before
    before = fits[0].day - timedelta(days=1)
    assert play_replay._context(dbs_market(dbs), before).vol_model is None
    assert play_replay._context(dbs_market(dbs), fits[3].day).vol_model.fit_day == fits[3].day
    look = vf.Lookup(conn)
    early = lotto_history.score("call", 0.2, 100.0, 105.0, 0.03, 0.25, vf.Vol(s, look.model(before)))
    assert early == lotto_history.score("call", 0.2, 100.0, 105.0, 0.03, 0.25)
    late = lotto_history.score("call", 0.2, 100.0, 105.0, 0.03, 0.25, vf.Vol(s, look.model(fits[-1].day)))
    assert late is not None and late != early


def dbs_market(dbs: StocksDatabases):  # type: ignore[no-untyped-def]
    from desk.stocks.repo import StocksMarket

    return StocksMarket(dbs.market)


def test_the_daily_step_refits_at_most_weekly_from_the_stored_table(dbs: StocksDatabases) -> None:
    conn = dbs.market
    panel = planted(stocks=32, sessions=400)
    at = "2026-01-01T00:00:00Z"
    for i, symbol in enumerate(panel.symbols):
        conn.execute("INSERT INTO stk_rv_symbols VALUES (?,?,?)", (symbol, vf._splits_key([]), at))
        conn.executemany("INSERT INTO stk_rv_daily VALUES (?,?,?,?,?,NULL,?)",
                         [(symbol, d.toordinal(), float(panel.rv[i, j]), float(panel.ret[i, j]), "daily", at)
                          for j, d in enumerate(panel.days)])
        conn.executemany("INSERT INTO stk_iv_daily VALUES (?,?,?,'massive_close',2,?)",
                         [(symbol, d.toordinal(), math.sqrt(float(panel.iv[i, j]) * 252), at)
                          for j, d in enumerate(panel.days)])
    now = cal.session_close(panel.days[-1]) + timedelta(hours=1)
    first = vf.step(conn, now, {})
    assert first["remaining"] == 0 and first["rows_written"] == 0 and first["refit"]["added"] > 0
    assert first["refit_for"] == vf.current_fit_day(now).isoformat()
    assert "refit" not in vf.step(conn, now, first)                            # once a week
    info = vf.latest(conn)
    expected = vf.walk_forward(panel)[-1]
    assert info is not None and info["fit_day"] == expected.day.isoformat()
    assert info["methods"] == expected.model.methods()
