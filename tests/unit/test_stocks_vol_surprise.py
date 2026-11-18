"""KESTREL's volatility-surprise model (desk.stocks.forecast.vol_surprise): a planted gap between realized and
implied volatility is learned, noise falls back to HAR-IV alone, and the baseline is the HAR-IV fit of its own day.
Synthetic data only."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

from desk.stocks import calendar as cal
from desk.stocks import vol_forecast as vf
from desk.stocks.forecast import vol_surprise as vs


@pytest.fixture
def small(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small trees so a walk-forward runs in seconds."""
    monkeypatch.setattr(vs, "PARAMS", {**vs.PARAMS, "max_iter": 30, "min_samples_leaf": 40})
    monkeypatch.setattr(vs, "HORIZONS", (1, 5))
    monkeypatch.setattr(vs, "MIN_TRAIN", 500)
    monkeypatch.setattr(vs, "GATE_ROWS", 500)


# ── the volatility surprise ──────────────────────────────────────────────────────────────────────
def _surprise_world(signal: float, seed: int) -> tuple[vf.Panel, dict[str, np.ndarray], list[tuple[date, vf.Model]]]:
    from tests.unit.test_stocks_vol_forecast import planted

    panel = planted(stocks=40, sessions=600, seed=seed)
    rng = np.random.default_rng(seed)
    skew = rng.standard_normal(panel.rv.shape)
    if signal:                                            # tomorrow's variance follows today's (unpriced) skew
        panel.rv[:, 1:] = panel.rv[:, 1:] * np.exp(signal * skew[:, :-1])
    side = {"term_slope": np.full(panel.rv.shape, np.nan), "skew": skew,
            "iv_spread": np.full(panel.rv.shape, np.nan), "earnings_days": np.full(panel.rv.shape, np.nan)}
    fits = [(f.day, f.model) for f in vf.walk_forward(panel)]
    return panel, side, fits


def test_vol_surprise_learns_a_planted_gap_and_falls_back_on_noise(small: None) -> None:
    panel, side, fits = _surprise_world(0.8, 11)
    data = vs.build(panel, side, fits)
    fit, ev, _ = vs.walk_forward(data, len(panel.days) - 1, panel.days[-1])
    assert ev["1"]["live"] and ev["1"]["skill"] > 0.05 and fit.horizons[1].live
    panel, side, fits = _surprise_world(0.0, 12)
    fit, ev, _ = vs.walk_forward(vs.build(panel, side, fits), len(panel.days) - 1, panel.days[-1])
    assert not any(e.get("live") for e in ev.values())


def test_the_har_baseline_uses_the_fit_of_its_own_day() -> None:
    days = cal.trading_days(date(2025, 1, 2), date(2025, 3, 31))
    coef = {"har_iv": {5: vf.Coef((0.0, 0.0, 0.0, 0.0, 1.0), 1.0, 100)}, "har_rv": {}}
    later = {"har_iv": {5: vf.Coef((1.0, 0.0, 0.0, 0.0, 1.0), 1.0, 100)}, "har_rv": {}}
    fits = [(days[10], vf.Model(days[10], "a", coef, {}, {})), (days[30], vf.Model(days[30], "b", later, {}, {}))]
    feats = [np.zeros((1, len(days)))] * 3
    b = vs.har_baseline(fits, days, feats, np.full((1, len(days)), -8.0), 5)
    assert np.isnan(b[0, 9]) and b[0, 10] == 0.0 and b[0, 29] == 0.0 and b[0, 30] == 1.0


def test_the_correction_is_one_unless_live_and_is_bounded() -> None:
    assert vs.Correction(1.0, False).multiplier == 1.0
    assert vs.Correction(0.2, True).multiplier == pytest.approx(math.exp(0.1))
    assert vs.Correction(5.0, True).multiplier == vs.BOUNDS[1]
    fit = vs.Fit(date(2026, 1, 5), {h: vs.HorizonFit(None, True) for h in vs.HORIZONS})
    s = vs.Session(date(2026, 1, 5), fit, None, {"A": {h: 0.1 * h for h in vs.HORIZONS}})
    assert s.at("A", 126).log_var == pytest.approx(6.3 * 63 / 126)       # type: ignore[union-attr]
    assert s.at("A", 3).log_var == pytest.approx(0.3)                    # type: ignore[union-attr]


