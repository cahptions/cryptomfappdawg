"""Chronological, read-only comparisons of allocations to recorded simulation sleeves."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize

from desk.stats.core import stationary_bootstrap_means
from desk.stocks import calendar as cal
from desk.stocks.paper.book import MICRO
from desk.stocks.repo import date_of

MODEL = "diagonal-shrinkage-min-variance-v1"
FIT_MIN = 40
TEST_MIN = 20
SHRINKAGE = .35
TRADING_DAYS = 252
FloatArray = NDArray[np.float64]
Curve = Mapping[date, float]


def ledger_curves(conn: sqlite3.Connection, accounts: Sequence[str], window: int
                  ) -> tuple[dict[str, dict[date, float]], set[date]]:
    curves = {}
    deposits: set[date] = set()
    for account in accounts:
        rows = conn.execute(
            "SELECT day,equity_micros FROM stk_equity WHERE account=? ORDER BY day DESC LIMIT ?",
            (account, window + 1),
        ).fetchall()
        curves[account] = {date_of(r[0]): r[1] / MICRO for r in rows}
        if rows:
            deposits.update(date_of(r[0]) for r in conn.execute(
                "SELECT DISTINCT day FROM stk_cash WHERE account=? AND day>=? AND reason='DEPOSIT'",
                (account, rows[-1][0]),
            ))
    return curves, deposits


def returns(curve: Curve) -> dict[date, float]:
    """Keep single-session moves only. An absent observation never becomes a flat day."""
    out = {}
    for day, value in curve.items():
        if not cal.is_trading_day(day):
            continue
        previous = curve.get(cal.previous_trading_day(day))
        if previous and previous > 0 and value > 0 and math.isfinite(previous) and math.isfinite(value):
            out[day] = value / previous - 1
    return out


def weights(fit: FloatArray, cap: float) -> tuple[FloatArray, FloatArray]:
    cov = np.asarray(np.cov(fit, rowvar=False), dtype=float)
    cov = (1 - SHRINKAGE) * cov + SHRINKAGE * np.diag(np.diag(cov))
    covariance = cov + np.eye(cov.shape[0]) * max(1e-12, float(np.trace(cov)) * 1e-8)
    # Normalize the objective so the solver's stopping threshold does not depend on return units.
    scaled = covariance / float(np.trace(covariance))
    uniform = np.full(len(scaled), 1 / len(scaled))
    result = minimize(lambda w: float(w @ scaled @ w), uniform, jac=lambda w: 2 * scaled @ w,
                      method="SLSQP", bounds=[(0., cap)] * len(uniform),
                      constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1, "jac": lambda w: np.ones_like(w)}],
                      options={"maxiter": 100, "ftol": 1e-10})
    allocation = np.asarray(result.x, dtype=float)
    if not result.success or not np.isfinite(allocation).all() or abs(float(allocation.sum()) - 1) > 1e-7:
        raise ValueError("The allocation solver did not find a feasible mix.")
    return allocation, covariance


def metrics(path: FloatArray, benchmark: FloatArray) -> dict[str, float | None]:
    before = np.r_[1., path[:-1]]
    returns_ = path / before - 1
    spy = benchmark / np.r_[1., benchmark[:-1]] - 1
    dd = (np.r_[1., path] / np.maximum.accumulate(np.r_[1., path])) - 1
    variance = float(np.var(spy))
    return {"return": float(path[-1] - 1), "drawdown": float(np.min(dd)),
            "volatility": float(np.std(returns_, ddof=1) * math.sqrt(TRADING_DAYS)),
            "worst_session": float(np.min(returns_)),
            "beta": float(np.cov(spy, returns_, ddof=0)[0, 1] / variance) if variance > 1e-15 else None}


def compare(curves: Mapping[str, Curve], benchmark: Curve, excluded: set[date], *, window: int,
            reserve: float, cap: float, entry_bps: int) -> dict[str, Any]:
    if not 2 <= len(curves) <= 8 or not 0 <= reserve <= .8 or not 0 <= entry_bps <= 100 or window < 1:
        raise ValueError("Choose two to eight strategies, up to 80% cash and up to 100 bp entry cost.")
    if not 0 < cap <= 1 or cap * len(curves) < 1 - 1e-10:
        raise ValueError("The per-strategy cap must allow the invested capital to be fully allocated.")
    per_sleeve = {sleeve: returns(curve) for sleeve, curve in sorted(curves.items())}
    spy = returns(benchmark)
    calendar = set(spy).union(*(set(series) for series in per_sleeve.values()))
    end = min(max(curve, default=date.max) for curve in curves.values())
    latest = sorted(day for day in calendar if day <= end)[-window:]
    days = [day for day in latest if day not in excluded and day in spy and all(day in series for series in per_sleeve.values())]
    fit_count = min(max(FIT_MIN, int(.7 * len(days))), len(days) - TEST_MIN) if len(days) >= FIT_MIN + TEST_MIN else 0
    coverage = {"matched": len(days), "considered": len(latest), "excluded": len(set(latest) & excluded),
                "per_strategy": {s: len(set(latest) & set(raw)) for s, raw in per_sleeve.items()},
                "benchmark": len(set(latest) & set(spy)),
                "needed": FIT_MIN + TEST_MIN}
    result: dict[str, Any] = {"model": MODEL, "coverage": coverage, "status": "insufficient",
                             "controls": {"reserve": reserve, "cap": cap, "entry_bps": entry_bps, "window": window}}
    if not fit_count:
        return result
    array = np.asarray([[series[day] for series in per_sleeve.values()] for day in days], dtype=float)
    allocation, covariance = weights(array[:fit_count], cap)
    holdout = array[fit_count:]
    levels = np.cumprod(1 + holdout, axis=0)
    market = np.cumprod(1 + np.asarray([spy[day] for day in days[fit_count:]], dtype=float))
    # Allocate before the first held-out return. The same reserve and initial costs apply to all invested mixes.
    invested = (1 - reserve) * (1 - entry_bps / 1e4)
    paths = {"risk": reserve + invested * (levels @ allocation),
             "equal": reserve + invested * levels.mean(axis=1),
             "market": reserve + invested * market,
             "cash": np.ones(len(market))}
    variance = float(allocation @ covariance @ allocation)
    marginal = covariance @ allocation
    # Components can be negative for hedging sleeves; they sum to one.
    shares = allocation * marginal / variance if variance > 0 else np.zeros_like(allocation)
    risk, equal = paths["risk"], paths["equal"]
    difference = risk / np.r_[1., risk[:-1]] - equal / np.r_[1., equal[:-1]]
    distribution = stationary_bootstrap_means(difference, resamples=500, seed=361)
    lo, hi = np.quantile(distribution, [.05, .95]) * 1e4
    inputs = {"model": MODEL, "controls": result["controls"], "strategies": list(per_sleeve),
              "days": [d.isoformat() for d in days], "sleeves": array.tolist(),
              "spy": [spy[d] for d in days]}
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    result.update(status="ready", fit={"first": days[0].isoformat(), "last": days[fit_count - 1].isoformat(), "n": fit_count},
                  test={"first": days[fit_count].isoformat(), "last": days[-1].isoformat(), "n": len(holdout)},
                  inputs_sha256=digest.hexdigest(),
                  allocations=[{"account": name, "weight": float(w * (1 - reserve)), "risk_share": float(r)}
                               for name, w, r in zip(per_sleeve, allocation, shares, strict=True)],
                  metrics={name: metrics(path, market) for name, path in paths.items()},
                  risk_vs_equal_bps={"mean": float(np.mean(difference) * 1e4), "low": float(lo), "high": float(hi)},
                  curve=[{"day": day.isoformat(), **{key: float(values[i]) for key, values in paths.items()}}
                         for i, day in enumerate(days[fit_count:])])
    return result
