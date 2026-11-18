"""The weekly refit of the forecast models, run in the Learner's niced training subprocess
(``learner.train.main``) before the Learner itself trains, so its features use the newest fits; also ``desk stocks
forecast-fit``. Each model fits once per fit day (the first session of the week, as ``vol_forecast``); a failure is
recorded and the other model still runs. ``bench`` measures one weekly fit on a synthetic universe."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import numpy as np

from desk.stocks import vol_forecast as vf
from desk.stocks.forecast import direction as dr
from desk.stocks.forecast import panel as pn
from desk.stocks.forecast import store


def due(market: Any, now: datetime) -> bool:
    """A model has no fit for this week's fit day yet."""
    target = vf.current_fit_day(now)
    return (store.newest_day(market, dr.MODEL, dr.VERSION) or date.min) < target


def run(market: Any, research: Any, now: datetime, *, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Fit both models for this week's fit day (each once); errors are reported per model, never raised."""
    fit_day = vf.current_fit_day(now)
    out: dict[str, Any] = {"fit_day": fit_day.isoformat()}
    work: dict[str, Callable[[], dict[str, Any]]] = {
        "direction": lambda: dr.refit(market, research, fit_day, now, progress=progress)}
    for name, fit in work.items():
        try:
            out[name] = fit()
        except (ValueError, ArithmeticError, MemoryError, OSError) as exc:
            out[name] = {"fitted": False, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
    return out


def summary_lines(market: Any) -> list[str]:
    """Plain lines for ``desk stocks forecast-fit``: each model's newest fit and its held-out check per horizon."""
    lines = []
    for title, model, version in (("Direction", dr.MODEL, dr.VERSION),):
        rec = store.latest(market, model, version)
        if rec is None:
            lines.append(f"{title}: no fit yet (the baseline is used: KESTREL behaves as before).")
            continue
        lines.append(f"{title}: fit of {rec.fit_day.isoformat()}, {rec.rows:,} rows, {rec.stocks} stocks, "
                     f"{rec.seconds:.0f} s, peak {rec.peak_mb or 0:.0f} MB.")
        for h, e in sorted(rec.evaluation.items(), key=lambda kv: int(kv[0])):
            if not e.get("rows"):
                lines.append(f"  {int(h):>2} sessions: {e.get('note', 'no held-out rows yet')} → baseline")
                continue
            if model == dr.MODEL:
                detail = (f"pinball {e['pinball']:.4f} vs {e['pinball_baseline']:.4f} baseline (t {e['t']}), "
                          f"calibration error {e['calibration_error']:.3f} vs {e['calibration_error_baseline']:.3f}, "
                          f"market-neutral rank correlation {e['neutral_ic']}")
            else:
                detail = f"squared error {e['mse']:.4f} vs {e['mse_har']:.4f} HAR-IV alone (t {e['t']})"
            lines.append(f"  {int(h):>2} sessions: {detail}; {e['rows']:,} held-out rows, {e['sessions']} sessions "
                         f"→ {'live' if e.get('live') else 'baseline'}")
    return lines


# ── a synthetic universe, for tests and the benchmark ─────────────────────────────────────────────
def synthetic(stocks: int, sessions: int, *, seed: int = 0, strength: float = 0.0, first: date = date(2014, 1, 2)
              ) -> pn.Panel:
    """Fat-tailed daily returns (Student t, 5 degrees of freedom) at stock-specific scales. ``strength`` plants a
    one-week reversal: each day's expected standardized return is −strength × the last five standardized shocks ÷ √5,
    which the 1-week return feature can learn; 0 is pure noise."""
    from desk.stocks import calendar as cal

    rng = np.random.default_rng(seed)
    days = cal.trading_days(first, date(first.year + 15, 12, 31))[:sessions]
    sigma = np.exp(rng.normal(math.log(0.02), 0.3, stocks))[:, None]
    eps = rng.standard_t(5, (stocks, sessions)) * math.sqrt(3 / 5)
    shocks = np.zeros((stocks, sessions))
    for t in range(sessions):
        recent = shocks[:, max(0, t - 5):t].sum(axis=1) / math.sqrt(5)
        shocks[:, t] = eps[:, t] - strength * recent
    r = sigma * shocks
    close = 50 * np.exp(np.cumsum(r, axis=1))
    dollars = close * np.exp(rng.normal(math.log(5e5), 0.5, (stocks, sessions)))
    market = 400 * np.exp(np.cumsum(0.01 * rng.standard_normal(sessions)))
    return pn.Panel([f"S{i:04d}" for i in range(stocks)], days, close, dollars, market,
                    np.broadcast_to(market, close.shape).copy(), {})


def bench(stocks: int = 1200, sessions: int = 2600, *, seed: int = 0,
          progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """One weekly direction fit on a synthetic universe of the default size: wall time and peak memory."""
    started = time.monotonic()
    p = synthetic(stocks, sessions, seed=seed, strength=0.1)
    built = time.monotonic()
    data = dr.dataset(p)
    sampled = time.monotonic()
    fit_col = len(p.days) - 1
    _, evaluation, used = dr.walk_forward(data, fit_col, p.days[-1], progress=progress)
    done = time.monotonic()
    return {"stocks": stocks, "sessions": sessions, "rows_sampled": len(data.rows), "rows_final": used,
            "seconds_panel": round(built - started, 1), "seconds_features": round(sampled - built, 1),
            "seconds_fit": round(done - sampled, 1), "seconds_total": round(done - started, 1),
            "peak_mb": round(dr.peak_mb(), 1), "threads": dr.THREADS,
            "live": {h: e.get("live") for h, e in evaluation.items()}}


__all__ = ["bench", "due", "run", "summary_lines", "synthetic"]
