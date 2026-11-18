"""KESTREL's direction model (desk.stocks.forecast.direction) and its panel: no look-ahead (planted future shocks),
sorted quantiles, calibration on a planted distribution, a gate that falls back to the baseline on noise, and stored
fits used only from their own day. Synthetic data only."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from desk.core.paths import DeskPaths
from desk.stocks.forecast import direction as dr
from desk.stocks.forecast import job, store
from desk.stocks.forecast import panel as pn
from desk.stocks.repo import StocksDatabases
from tests.conftest import REPO


@pytest.fixture
def small(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small trees and blocks so a walk-forward runs in seconds."""
    monkeypatch.setattr(dr, "PARAMS", {**dr.PARAMS, "max_iter": 15, "min_samples_leaf": 50})
    monkeypatch.setattr(dr, "HORIZONS", (1, 5))
    monkeypatch.setattr(dr, "EVAL_BLOCKS", 2)
    monkeypatch.setattr(dr, "BLOCK", 126)
    monkeypatch.setattr(dr, "MIN_TRAIN", 1000)
    monkeypatch.setattr(dr, "GATE_ROWS", 1000)


# ── the panel ────────────────────────────────────────────────────────────────────────────────────
def test_features_never_see_the_decision_session_or_later() -> None:
    p = job.synthetic(30, 400, seed=3)
    rows, cols = np.arange(30), np.full(30, 350)
    before = pn.matrix(pn.features(p, rows, cols))
    p.close[:, 350:] *= 5.0                               # a planted shock on the decision session and after
    p.dollars[:, 350:] *= 50.0
    p.market[350:] *= 0.1
    p.side = {"committee": np.where(np.arange(400) >= 350, 99.0, np.nan)[None, :].repeat(30, 0)}
    after = pn.matrix(pn.features(p, rows, cols))
    np.testing.assert_array_equal(np.nan_to_num(before, nan=-7), np.nan_to_num(after, nan=-7))


def test_rolling_helpers() -> None:
    x = np.array([[1.0, 5.0, np.nan, 2.0, 7.0, 3.0, 1.0, 0.0]])
    np.testing.assert_allclose(pn.rolling_max(x, 3)[0, 2:], [5, 5, 7, 7, 7, 3])
    m = pn.rolling_mean(x, 4, share=0.75)
    assert math.isnan(m[0, 2]) and m[0, 3] == pytest.approx(8 / 3) and m[0, 7] == pytest.approx(11 / 4)


def test_standardized_target_uses_the_previous_close_scale() -> None:
    p = job.synthetic(5, 300, seed=4)
    z, scale = pn.standardized_returns(p, np.array([0]), np.array([280]), 5)
    ref = pn.ref_variance(p.close)[0, 279]
    assert scale[0] == pytest.approx(math.sqrt(ref * 5))
    assert z[0] == pytest.approx(math.log(p.close[0, 285] / p.close[0, 280]) / scale[0])


# ── the direction model ──────────────────────────────────────────────────────────────────────────
def test_quantiles_come_out_sorted_and_calibrate_on_a_planted_distribution() -> None:
    rng = np.random.default_rng(0)
    z = rng.standard_normal(20000)
    true = np.broadcast_to(np.asarray([-1.6449, -1.2816, -0.6745, 0.0, 0.6745, 1.2816, 1.6449]), (len(z), 7))
    sessions = np.repeat(np.arange(200), 100)
    stocks = np.tile(np.arange(100), 200)
    e = dr.evaluate(z, true, true * 1.5, sessions, stocks, 1)
    assert e["coverage"] == pytest.approx(list(dr.QUANTILES), abs=0.012)
    assert e["calibration_error"] < 0.01 < e["calibration_error_baseline"]
    assert e["pinball"] < e["pinball_baseline"] and e["live"]
    # the same forecast as the baseline is never live
    assert not dr.evaluate(z, true, true, sessions, stocks, 1)["live"]


def test_a_planted_reversal_goes_live_and_noise_falls_back(small: None) -> None:
    p = job.synthetic(60, 700, seed=5, strength=0.4)
    data = dr.dataset(p)
    fit, ev, _ = dr.walk_forward(data, len(p.days) - 1, p.days[-1])
    assert ev["1"]["live"] and ev["1"]["neutral_ic"] > 0.05 and fit.horizons[1].live
    x = data.x[:200]
    q = fit.predict(x, 1)
    assert np.all(np.diff(q, axis=1) >= 0)                  # never crossing
    noise = job.synthetic(60, 700, seed=6, strength=0.0)
    fit2, ev2, _ = dr.walk_forward(dr.dataset(noise), len(noise.days) - 1, noise.days[-1])
    assert not any(e["live"] for e in ev2.values()) and not any(h.live for h in fit2.horizons.values())


def test_a_fit_never_trains_on_outcomes_after_its_day(small: None) -> None:
    p = job.synthetic(40, 650, seed=7)
    fit_col = 600
    base = dr.walk_forward(dr.dataset(p), fit_col, p.days[fit_col])[0]
    p.close[:, fit_col:] *= np.exp(np.linspace(0, 3, p.close.shape[1] - fit_col))   # a planted future rally
    shocked = dr.walk_forward(dr.dataset(p), fit_col, p.days[fit_col])[0]
    for h in (1, 5):
        assert base.horizons[h].baseline == pytest.approx(shocked.horizons[h].baseline)
    x = dr.dataset(job.synthetic(10, 400, seed=8)).x
    np.testing.assert_allclose(base.predict(x, 5), shocked.predict(x, 5))


def test_horizons_between_the_fitted_ones_interpolate() -> None:
    fit = dr.Fit(date(2026, 1, 5), pn.NAMES)
    for h in dr.HORIZONS:
        fit.horizons[h] = dr.HorizonFit([], [-1.6, -1.3, -0.7, 0.0, 0.7, 1.3, 1.6], h <= 5)
    s = dr.Session(date(2026, 1, 5), fit, None, {"A": {h: (np.full(7, float(h)), h <= 5) for h in dr.HORIZONS}},
                   {"A": 0.02})
    q = s.at("A", 3)
    assert q is not None and q.cond[0] == pytest.approx(3.0) and q.live
    q7 = s.at("A", 7)
    assert q7 is not None and q7.cond[0] == pytest.approx(7.0) and not q7.live
    assert s.at("A", 200).cond[0] == pytest.approx(63.0) and s.at("B", 5) is None   # type: ignore[union-attr]


# ── stored fits ──────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def dbs(tmp_path: Path):  # type: ignore[no-untyped-def]
    out = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    yield out
    out.close()


def test_fits_are_append_only_and_used_only_from_their_day(dbs: StocksDatabases, monkeypatch: pytest.MonkeyPatch
                                                           ) -> None:
    monkeypatch.setattr(store, "KEEP", 2)
    now = datetime(2026, 1, 20, tzinfo=UTC)
    days = [date(2026, 1, 5) + timedelta(days=7 * k) for k in range(3)]
    for k, d in enumerate(days):
        assert store.save(dbs.market, "direction", 1, d, {"k": k}, {1: True}, {}, 10, 2, 1.0, None, now)
    assert not store.save(dbs.market, "direction", 1, days[0], {"k": 9}, {}, {}, 1, 1, 1.0, None, now)
    loader = store.Loader(dbs.market, "direction", 1)
    assert loader.on(days[0] - timedelta(days=1)) is None
    assert loader.on(days[0]) is None                     # its models were pruned: the baseline, never a later fit
    got = loader.on(days[1] + timedelta(days=3))
    assert got is not None and got[1] == {"k": 1} and got[0].live == {1: True}
    assert store.latest(dbs.market, "direction", 1).fit_day == days[2]   # type: ignore[union-attr]
    with pytest.raises(Exception, match="append-only"):
        dbs.market.execute("DELETE FROM stk_forecast_fits")

