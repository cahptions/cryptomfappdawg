"""Skill beyond the market's direction (docs/STOCKS_LEARNER.md → Raw and market-neutral skill).

An option's return held to expiry moves with its stock, and its stock with the market and its sector: a call bought
before a rally makes money whoever picked it. To see skill rather than the market's direction, each candidate carries
the **same-horizon** returns of SPY and of its sector's fund (``universe.SECTOR_FUNDS``) from its decision session's
close to the close of the session its label became known (stored daily closes; missing when either close is not
stored, never estimated).

**Neutralising.** Within each structure (a call and a put have opposite exposures) the realised return is regressed
on the market return and the sector's return over the market's, by ordinary least squares on the *training* rows only;
the neutral return is the realised one less those fitted market and sector parts (the intercept is kept). A pure
market-beta effect leaves nothing behind. Rows without the market's return have no neutral return.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

import numpy as np

MIN_ROWS = 30                       # rows a structure needs for its own betas (else: no neutralising for it)


def horizon_returns(market: sqlite3.Connection | None, sessions: np.ndarray, label_days: np.ndarray,
                    sectors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(SPY's return, the sector fund's return) from each row's decision session to its label day; NaN when a close
    is not stored or the label is unknown."""
    from desk.stocks.learner.features import SECTORS
    from desk.stocks.universe import SECTOR_FUNDS

    n = len(sessions)
    mkt, sec = np.full(n, np.nan, np.float32), np.full(n, np.nan, np.float32)
    known = label_days > 0
    if market is None or not known.any():
        return mkt, sec
    funds = sorted(set(SECTOR_FUNDS.values()) | {"SPY"})
    lo, hi = int(sessions[known].min()), int(label_days[known].max())
    closes: dict[str, dict[int, float]] = {f: {} for f in funds}
    marks = ",".join("?" * len(funds))
    for symbol, day, close in market.execute(
            f"SELECT symbol, day, close FROM stk_bars WHERE symbol IN ({marks}) AND day>=? AND day<=?",  # noqa: S608
            (*funds, lo, hi)):
        if close:
            closes[symbol][int(day)] = float(close)

    def ret(symbol: str, a: int, b: int) -> float:
        x, y = closes[symbol].get(a), closes[symbol].get(b)
        return np.nan if x is None or y is None else y / x - 1

    for i in np.where(known)[0]:
        a, b = int(sessions[i]), int(label_days[i])
        mkt[i] = ret("SPY", a, b)
        code = sectors[i]
        if np.isfinite(code) and 0 <= int(code) < len(SECTORS):
            sec[i] = ret(SECTOR_FUNDS.get(SECTORS[int(code)], "SPY"), a, b)
    return mkt, sec


def betas(y: np.ndarray, mkt: np.ndarray, sec: np.ndarray, structure: np.ndarray, rows: np.ndarray
          ) -> dict[int, tuple[float, float, float]]:
    """Per structure: (intercept, market beta, sector beta) fitted on ``rows`` (a boolean mask)."""
    out: dict[int, tuple[float, float, float]] = {}
    usable = rows & np.isfinite(y) & np.isfinite(mkt)
    for s in np.unique(structure[usable & np.isfinite(structure)]):
        r = usable & (structure == s)
        if r.sum() < MIN_ROWS:
            continue
        rel = np.where(np.isfinite(sec[r]), sec[r] - mkt[r], 0.0)
        A = np.column_stack([np.ones(int(r.sum())), mkt[r], rel])
        coef, *_ = np.linalg.lstsq(A, y[r].astype(float), rcond=None)
        out[int(s)] = (float(coef[0]), float(coef[1]), float(coef[2]))
    return out


def neutralise(y: np.ndarray, mkt: np.ndarray, sec: np.ndarray, structure: np.ndarray,
               fitted: Mapping[int, tuple[float, float, float]]) -> np.ndarray:
    """``y`` less the fitted market and sector parts; NaN without the market's return; unchanged for a structure
    without betas."""
    out = np.full(len(y), np.nan, np.float32)
    ok = np.isfinite(y) & np.isfinite(mkt)
    rel = np.where(np.isfinite(sec), sec - mkt, 0.0)
    out[ok] = y[ok]
    for s, (_, b_mkt, b_sec) in fitted.items():
        r = ok & (structure == s)
        out[r] = y[r] - b_mkt * mkt[r] - b_sec * rel[r]
    return out


__all__ = ["MIN_ROWS", "betas", "horizon_returns", "neutralise"]
