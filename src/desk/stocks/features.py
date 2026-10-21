"""Price features for every stock at one day, from the split-adjusted panel (vectorized; NaN when not computable).

Everything at index ``t`` uses bars up to and including ``t`` (the close of the day being decided on); nothing
looks forward.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np

from desk.stocks.prices import Panel

Array = np.ndarray
TRADING_YEAR = 252


def _window(values: Array, t: int, n: int) -> Array:
    lo = max(0, t - n + 1)
    return values[:, lo:t + 1]


def _nan_stat(fn: str, values: Array) -> Array:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        result: Array = getattr(np, fn)(values, axis=1)
    return result


def _ret(close: Array, t: int, lag: int) -> Array:
    if t - lag < 0:
        return np.full(close.shape[0], np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out: Array = close[:, t] / close[:, t - lag] - 1
    return out


@dataclass
class PriceFeatures:
    symbols: list[str]
    t: int
    values: dict[str, Array]

    def get(self, name: str, symbol: str) -> float | None:
        i = self.index.get(symbol)
        if i is None:
            return None
        v = float(self.values[name][i])
        return None if np.isnan(v) else v

    @property
    def index(self) -> dict[str, int]:
        return {s: i for i, s in enumerate(self.symbols)}


def compute(panel: Panel, t: int, benchmark: str = "SPY") -> PriceFeatures:
    c, o, v, h, lo = panel.close, panel.open, panel.volume, panel.high, panel.low
    n = c.shape[0]
    with np.errstate(divide="ignore", invalid="ignore"):
        daily = np.full(c.shape, np.nan)
        daily[:, 1:] = c[:, 1:] / c[:, :-1] - 1
    f: dict[str, Array] = {}
    f["close"] = c[:, t] if t >= 0 else np.full(n, np.nan)
    for name, lag in (("ret_1d", 1), ("ret_5d", 5), ("ret_21d", 21), ("ret_63d", 63), ("ret_126d", 126), ("ret_252d", 252)):
        f[name] = _ret(c, t, lag)
    f["mom_12_1"] = (np.full(n, np.nan) if t - 252 < 0 else c[:, t - 21] / c[:, t - 252] - 1)
    f["sma50"] = _nan_stat("nanmean", _window(c, t, 50))
    f["sma200"] = _nan_stat("nanmean", _window(c, t, 200))
    f["bars_200"] = np.sum(~np.isnan(_window(c, t, 200)), axis=1).astype(float)
    year = _window(c, t, TRADING_YEAR)
    f["high_52w"] = _nan_stat("nanmax", _window(h, t, TRADING_YEAR))
    f["low_52w"] = _nan_stat("nanmin", _window(lo, t, TRADING_YEAR))
    with np.errstate(divide="ignore", invalid="ignore"):
        f["dist_high_52w"] = f["close"] / f["high_52w"] - 1
        f["dist_low_52w"] = f["close"] / f["low_52w"] - 1
        f["above_sma50"] = f["close"] / f["sma50"] - 1
        f["above_sma200"] = f["close"] / f["sma200"] - 1
    f["vol_20d"] = _nan_stat("nanstd", _window(daily, t, 20)) * np.sqrt(TRADING_YEAR)
    f["vol_60d"] = _nan_stat("nanstd", _window(daily, t, 60)) * np.sqrt(TRADING_YEAR)
    f["vol_1y"] = _nan_stat("nanstd", _window(daily, t, TRADING_YEAR)) * np.sqrt(TRADING_YEAR)
    running_max = np.fmax.accumulate(np.where(np.isnan(year), -np.inf, year), axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdowns = year / running_max - 1
    f["max_drawdown_1y"] = _nan_stat("nanmin", np.where(np.isfinite(drawdowns), drawdowns, np.nan))
    dv = _window(c * v, t, 20)
    f["dollar_volume_20d"] = _nan_stat("nanmedian", dv)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["rel_volume"] = v[:, t] / _nan_stat("nanmedian", _window(v, t - 1, 20)) if t >= 1 else np.full(n, np.nan)
        f["gap"] = o[:, t] / c[:, t - 1] - 1 if t >= 1 else np.full(n, np.nan)
    f["bars_year"] = np.sum(~np.isnan(year), axis=1).astype(float)
    # beta and relative strength against the benchmark
    bi = panel.index.get(benchmark)
    f["beta_1y"] = np.full(n, np.nan)
    f["rs_63d"] = np.full(n, np.nan)
    if bi is not None:
        b = _window(daily, t, TRADING_YEAR)[bi]
        rows = _window(daily, t, TRADING_YEAR)
        ok_b = ~np.isnan(b)
        for i in range(n):
            ok = ok_b & ~np.isnan(rows[i])
            if ok.sum() >= 60:
                x, y = b[ok], rows[i][ok]
                var = float(np.var(x))
                f["beta_1y"][i] = float(np.cov(x, y, bias=True)[0, 1] / var) if var > 0 else np.nan
        f["rs_63d"] = f["ret_63d"] - f["ret_63d"][bi]
    return PriceFeatures(panel.symbols, t, f)


def sector_relative(values: Array, sectors: list[str]) -> Array:
    """Each value minus its sector's median (sectors with fewer than 5 members use the whole universe)."""
    out = np.full(values.shape, np.nan)
    overall = float(np.nanmedian(values)) if np.any(~np.isnan(values)) else np.nan
    by: dict[str, list[int]] = {}
    for i, s in enumerate(sectors):
        by.setdefault(s, []).append(i)
    for idx in by.values():
        vals = values[idx]
        med = float(np.nanmedian(vals)) if np.sum(~np.isnan(vals)) >= 5 else overall
        out[idx] = vals - med
    return out


__all__ = ["TRADING_YEAR", "PriceFeatures", "compute", "sector_relative"]
