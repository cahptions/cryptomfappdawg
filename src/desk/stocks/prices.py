"""The price panel: stored end-of-day bars as split-adjusted arrays (symbols × trading days), never filled.

A missing bar stays NaN. Splits are applied when reading (a bar before a split's execution date is divided by the
split ratio), so history stored before a split is announced is never rewritten. Prices are not adjusted for
dividends; benchmark comparisons use the same price-only returns on both sides (docs/STOCKS_METHOD.md).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from desk.stocks.contracts import Split
from desk.stocks.repo import StocksMarket, date_of, day_of

Array = np.ndarray


@dataclass
class Panel:
    symbols: list[str]
    days: list[date]
    open: Array
    high: Array
    low: Array
    close: Array
    volume: Array
    index: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = {s: i for i, s in enumerate(self.symbols)}

    @property
    def dollar_volume(self) -> Array:
        product: Array = self.close * self.volume
        return product

    def day_index(self, d: date) -> int:
        """Index of the last trading day on or before ``d`` (-1 if none)."""
        lo, hi = 0, len(self.days) - 1
        found = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.days[mid] <= d:
                found, lo = mid, mid + 1
            else:
                hi = mid - 1
        return found

    def series(self, symbol: str, field_name: str = "close") -> Array:
        i = self.index.get(symbol)
        if i is None:
            return np.full(len(self.days), np.nan)
        values: Array = getattr(self, field_name)[i]
        return values


def split_factors(splits: Sequence[Split], symbols: Sequence[str], days: Sequence[date]) -> Array:
    """Cumulative divisor per (symbol, day): prices on days before a split's execution date are divided by its ratio
    (volumes multiplied)."""
    factors = np.ones((len(symbols), len(days)))
    index = {s: i for i, s in enumerate(symbols)}
    ords = np.array([d.toordinal() for d in days])
    for s in splits:
        i = index.get(s.symbol)
        if i is None or s.ratio <= 0:
            continue
        factors[i, ords < s.execution_date.toordinal()] *= s.ratio
    return factors


def load_panel(market: StocksMarket, since: date, until: date | None = None, symbols: Sequence[str] | None = None,
               *, adjust: bool = True) -> Panel:
    days = [d for d in market.bar_days() if d >= since and (until is None or d <= until)]
    wanted = set(symbols) if symbols is not None else None
    rows: dict[str, list[tuple[int, float, float, float, float, float]]] = defaultdict(list)
    for r in market.bar_rows(since, until):
        if wanted is None or r[0] in wanted:
            rows[r[0]].append((r[1], r[2], r[3], r[4], r[5], r[6]))
    names = sorted(rows) if symbols is None else [s for s in symbols if s in rows]
    col = {day_of(d): j for j, d in enumerate(days)}
    shape = (len(names), len(days))
    o, h, lo, c, v = (np.full(shape, np.nan) for _ in range(5))
    for i, name in enumerate(names):
        for day, op, hi, low, cl, vol in rows[name]:
            j = col.get(day)
            if j is not None:
                o[i, j], h[i, j], lo[i, j], c[i, j], v[i, j] = op, hi, low, cl, vol
    if adjust and names and days:
        f = split_factors(market.splits(), names, days)
        o, h, lo, c, v = o / f, h / f, lo / f, c / f, v * f
    return Panel(names, days, o, h, lo, c, v)


def returns(close: Array, lag: int, end: int) -> Array:
    """Simple return from ``end - lag`` to ``end`` per row (NaN when either side is missing)."""
    if end - lag < 0:
        return np.full(close.shape[0], np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(close[:, end] / close[:, end - lag] - 1)


def last_valid(values: Array, end: int, max_gap: int = 5) -> Array:
    """Each row's latest value at or before ``end`` within ``max_gap`` days (NaN beyond)."""
    out = np.full(values.shape[0], np.nan)
    for back in range(max_gap + 1):
        j = end - back
        if j < 0:
            break
        take = np.isnan(out) & ~np.isnan(values[:, j])
        out[take] = values[take, j]
    return out


def daily_returns(close: Array) -> Array:
    out = np.full(close.shape, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[:, 1:] = close[:, 1:] / close[:, :-1] - 1
    return out


def ordinal_days(days: Sequence[date]) -> Array:
    return np.array([d.toordinal() for d in days])


__all__ = ["Panel", "daily_returns", "date_of", "last_valid", "load_panel", "ordinal_days", "returns", "split_factors"]
