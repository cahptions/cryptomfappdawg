"""The daily stock panel the direction model learns from, and its point-in-time features.

**The panel.** Stocks × consecutive trading sessions: split-adjusted closes (the stored splits applied when read, as
everywhere in KESTREL) and dollar volume from ``stk_bars``, plus SPY and the sector funds (``universe.SECTOR_FUNDS``)
for the market and sector returns. A missing bar is NaN, never filled.

**When a feature is known.** A decision on session S is made after S's close, from S's own option quotes; every
feature here is read as of the **previous session's close** (column S − 1), the Learner's rule, so the training rows and
the live ones are built by the same code from the same columns. The side data are point in time too: desk scores
(``stk_score_history``, dated before S), option signals (``stk_option_signals`` dated before S, newest within
``FRESH`` sessions), the next results date (an announced date first seen before S, else the last results filing
(8-K item 2.02) before S plus 91 days).

**Features** (``NAMES``): 1-week and 1-month returns (short-term reversal), 12-1 momentum (Jegadeesh–Titman), nearness
to the 52-week high (George–Hwang), the stock's realized volatility over a day, a week and a month (the HAR inputs,
from daily returns as ``vol_forecast`` uses where minute bars are missing) and its reference scale, dollar volume and
its recent surge, SPY's and the sector fund's returns and the stock's return against its sector, the committee score,
three option signals (call−put IV spread, OTM put skew, IV / realized vol) and days to the next results.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np

Array = np.ndarray
VAR_FLOOR = 1e-6                # a 0.1% daily move, as vol_forecast
REF_SESSIONS = 63               # the reference scale: root mean square daily return over a quarter
MIN_SHARE = 0.8                 # of a window's sessions present before a windowed feature counts
FRESH = 5                       # sessions a side observation (score, option signal) stays current
EARNINGS_CYCLE = 91             # days from one results filing to the next, when no date is announced
NAMES = ("ret_5d", "ret_1m", "mom_12_1", "high_52w", "log_rv_day", "log_rv_week", "log_rv_month", "log_sigma_ref",
         "rv_ratio", "log_dollar_volume", "volume_surge", "mkt_ret_5d", "mkt_ret_1m", "mkt_log_rv_month",
         "sector_ret_1m", "rel_sector_1m", "committee", "opt_iv_spread", "opt_skew", "opt_iv_rv", "earnings_days")


@dataclass
class Panel:
    symbols: list[str]
    days: list[date]                         # consecutive trading sessions
    close: Array                             # stocks × sessions, split-adjusted, NaN missing
    dollars: Array                           # close × volume
    market: Array                            # SPY's closes (sessions), NaN missing
    sector: Array                            # per stock: its sector fund's closes (stocks × sessions)
    side: dict[str, Array] = field(default_factory=dict)    # as-of-close side data (stocks × sessions)

    def col(self, d: date) -> int | None:
        """The column of session ``d`` (None when outside the panel)."""
        if not self.days or d < self.days[0] or d > self.days[-1]:
            return None
        lo, hi = 0, len(self.days) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.days[mid] == d:
                return mid
            if self.days[mid] < d:
                lo = mid + 1
            else:
                hi = mid - 1
        return None


# ── loading ──────────────────────────────────────────────────────────────────────────────────────
def _chunks(items: Sequence[str], n: int = 500) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


def candidates(conn: sqlite3.Connection, limit: int, *, min_sessions: int = 300) -> list[str]:
    """The stocks to learn from: those with at least ``min_sessions`` stored bars, the ``limit`` with the highest
    average dollar volume over their whole stored history (not a recent window, so the choice does not favour what
    happened to do well lately). ETFs and test listings are left out."""
    skip = {r[0] for r in conn.execute("SELECT symbol FROM stk_listings WHERE etf=1 OR test=1")}
    rows = conn.execute("SELECT symbol, COUNT(*) n, AVG(close*volume) dv FROM stk_bars GROUP BY symbol").fetchall()
    ok = [(r[0], float(r[2] or 0.0)) for r in rows if r[1] >= min_sessions and r[0] not in skip]
    ok.sort(key=lambda x: (-x[1], x[0]))
    return [s for s, _ in ok[:limit]]


def sector_funds(conn: sqlite3.Connection, symbols: Sequence[str]) -> dict[str, str]:
    """Each stock's sector fund (its SIC's Fama-French sector; unknown: SPY)."""
    from desk.stocks.sectors import sector
    from desk.stocks.universe import SECTOR_FUNDS

    out = {s: "SPY" for s in symbols}
    for chunk in _chunks(list(symbols)):
        marks = ",".join("?" * len(chunk))           # placeholders only; values are bound
        for symbol, sic in conn.execute(f"SELECT l.symbol, c.sic FROM stk_listings l JOIN stk_companies c ON "  # noqa: S608
                                        f"c.cik=l.cik WHERE l.symbol IN ({marks})", tuple(chunk)):
            out[symbol] = SECTOR_FUNDS.get(sector(sic), "SPY")
    return out


def _matrix(conn: sqlite3.Connection, symbols: Sequence[str], first: int, last: int, col: dict[int, int],
            n_days: int) -> tuple[Array, Array]:
    close = np.full((len(symbols), n_days), np.nan)
    dollars = np.full((len(symbols), n_days), np.nan)
    index = {s: i for i, s in enumerate(symbols)}
    ordinals = np.asarray(sorted(col))
    for chunk in _chunks(list(symbols)):
        marks = ",".join("?" * len(chunk))           # placeholders only; values are bound
        for symbol, day, c, v in conn.execute(
                f"SELECT symbol, day, close, volume FROM stk_bars WHERE day>=? AND day<=? AND symbol IN ({marks})",  # noqa: S608
                (first, last, *chunk)):
            j = col.get(int(day))
            if j is not None and c and c > 0:
                close[index[symbol], j] = c
                dollars[index[symbol], j] = c * (v or 0.0)
        for symbol, execution, a, b in conn.execute(
                f"SELECT symbol, execution_date, split_from, split_to FROM stk_splits WHERE symbol IN ({marks})",  # noqa: S608
                tuple(chunk)):
            if a and b and a > 0 and b > 0:
                cut = int(np.searchsorted(ordinals, int(execution)))          # the columns before the split
                close[index[symbol], :cut] /= b / a
    return close, dollars


def load(conn: sqlite3.Connection, symbols: Sequence[str], last: date, *, sessions: int | None = None,
         research: sqlite3.Connection | None = None) -> Panel:
    """The panel for ``symbols`` through ``last`` (``sessions`` columns, or every stored session)."""
    from desk.stocks import calendar as cal

    if sessions is None:
        row = conn.execute("SELECT MIN(day) FROM stk_bars").fetchone()
        first = date.fromordinal(int(row[0])) if row and row[0] else last
    else:
        first = last - timedelta(days=int(sessions * 7 / 5) + 10)
    days = cal.trading_days(first, last)
    if sessions is not None:
        days = days[-sessions:]
    symbols = list(symbols)
    if not days:
        empty = np.zeros((len(symbols), 0))
        return Panel(symbols, [], empty, empty.copy(), np.zeros(0), empty.copy())
    col = {d.toordinal(): j for j, d in enumerate(days)}
    lo, hi = days[0].toordinal(), days[-1].toordinal()
    close, dollars = _matrix(conn, symbols, lo, hi, col, len(days))
    funds = sector_funds(conn, symbols)
    extra = sorted({*funds.values(), "SPY"})
    fund_close, _ = _matrix(conn, extra, lo, hi, col, len(days))
    at = {s: i for i, s in enumerate(extra)}
    p = Panel(symbols, days, close, dollars, fund_close[at["SPY"]],
              np.stack([fund_close[at[funds[s]]] for s in symbols]) if symbols else np.zeros((0, len(days))))
    p.side = side_data(conn, research, symbols, days)
    return p


# ── point-in-time side data, as of each session's close ──────────────────────────────────────────
def _carry(x: Array, sessions: int = FRESH) -> Array:
    """Each value carried forward for at most ``sessions`` further sessions (a stale observation becomes missing)."""
    out = x.copy()
    last = np.where(np.isfinite(x), np.arange(x.shape[1])[None, :], -10**9)
    last = np.maximum.accumulate(last, axis=1)
    idx = np.clip(last, 0, None)
    filled = np.take_along_axis(x, idx, axis=1)
    age = np.arange(x.shape[1])[None, :] - last
    out = np.where(age <= sessions, filled, np.nan)
    return out


def side_data(market: sqlite3.Connection, research: sqlite3.Connection | None, symbols: Sequence[str],
              days: Sequence[date]) -> dict[str, Array]:
    """Committee score, option signals and days to the next results, as of each session's close (NaN unknown)."""
    from desk.stocks.option_signals import MIN_LIQUID

    n, m = len(symbols), len(days)
    index = {s: i for i, s in enumerate(symbols)}
    col = {d.toordinal(): j for j, d in enumerate(days)}
    out = {k: np.full((n, m), np.nan) for k in ("committee", "opt_iv_spread", "opt_skew", "opt_iv_rv")}
    if not n or not m:
        out["earnings_days"] = np.full((n, m), np.nan)
        return out
    lo, hi = days[0].toordinal(), days[-1].toordinal()
    if research is not None:
        try:
            for asof, symbol, c in research.execute(
                    "SELECT asof, symbol, json_extract(scores_json, '$.c') FROM stk_score_history WHERE asof>=? AND "
                    "asof<=?", (lo, hi)):
                i, j = index.get(symbol), col.get(int(asof))
                if i is not None and j is not None and c is not None:
                    out["committee"][i, j] = float(c)
        except sqlite3.OperationalError:
            pass
    try:
        for symbol, day, spread, skew, iv30, rv22 in market.execute(
                "SELECT symbol, day, iv_spread, skew, iv30, rv22 FROM stk_option_signals WHERE day>=? AND day<=? AND "
                "liquid>=?", (lo, hi, MIN_LIQUID)):
            i, j = index.get(symbol), col.get(int(day))
            if i is None or j is None:
                continue
            if spread is not None:
                out["opt_iv_spread"][i, j] = spread
            if skew is not None:
                out["opt_skew"][i, j] = skew
            if iv30 and rv22:
                out["opt_iv_rv"][i, j] = iv30 / rv22
    except sqlite3.OperationalError:
        pass
    out = {k: _carry(v) for k, v in out.items()}
    out["earnings_days"] = earnings_days(market, symbols, days)
    return out


def earnings_days(conn: sqlite3.Connection, symbols: Sequence[str], days: Sequence[date]) -> Array:
    """Calendar days from each session to the stock's next results as known at that session's close: an announced
    date first seen by then (and still ahead), else the last results filing filed by then + 91 days (rolled forward
    in 91-day steps)."""
    from desk.stocks import calendar as cal

    n, m = len(symbols), len(days)
    out = np.full((n, m), np.nan)
    if not n or not m:
        return out
    ordinals = np.asarray([d.toordinal() for d in days])
    index = {s: i for i, s in enumerate(symbols)}
    filings: dict[str, list[int]] = defaultdict(list)
    announced: dict[str, list[tuple[int, int]]] = defaultdict(list)
    try:
        for chunk in _chunks(list(symbols)):
            marks = ",".join("?" * len(chunk))       # placeholders only; values are bound
            for symbol, filed in conn.execute(
                    f"SELECT l.symbol, f.filed FROM stk_filings f JOIN stk_listings l ON l.cik=f.cik WHERE "  # noqa: S608
                    f"f.form IN ('8-K','8-K/A') AND f.items LIKE '%2.02%' AND l.symbol IN ({marks})", tuple(chunk)):
                filings[symbol].append(int(filed))
            for symbol, report, seen in conn.execute(
                    f"SELECT symbol, report_date, first_seen FROM stk_earnings_dates WHERE symbol IN ({marks})",  # noqa: S608
                    tuple(chunk)):
                seen_day = cal.new_york(datetime.fromisoformat(seen)).date().toordinal()
                announced[symbol].append((int(report), seen_day))
    except sqlite3.OperationalError:
        return out
    for symbol, filed in filings.items():
        f = np.asarray(sorted(set(filed)))
        k = np.searchsorted(f, ordinals, side="right") - 1          # the last filing on or before the session
        have = k >= 0
        since = np.where(have, ordinals - f[np.clip(k, 0, None)], np.nan)
        out[index[symbol]] = np.where(have, EARNINGS_CYCLE - np.mod(since, EARNINGS_CYCLE), np.nan)
    for symbol, reports in announced.items():
        nearest = np.full(m, np.inf)
        for report, seen in reports:
            known = (ordinals >= seen) & (ordinals < report)
            nearest = np.where(known, np.minimum(nearest, report - ordinals), nearest)
        row = out[index[symbol]]
        row[:] = np.where(np.isfinite(nearest), nearest, row)   # an announced date beats the estimate
    return out


# ── windowed helpers ─────────────────────────────────────────────────────────────────────────────
def _shift(x: Array, k: int) -> Array:
    """x[:, d − k] at column d (NaN before the start)."""
    out = np.full(x.shape, np.nan)
    if k < x.shape[-1]:
        out[..., k:] = x[..., :x.shape[-1] - k]
    return out


def rolling_mean(x: Array, w: int, share: float = MIN_SHARE) -> Array:
    """Mean over columns d − w + 1 .. d of the present values, when at least ``share`` of them are present."""
    valid = np.isfinite(x)
    zero = np.zeros((*x.shape[:-1], 1))
    c = np.concatenate([zero, np.cumsum(np.where(valid, x, 0.0), axis=-1)], axis=-1)
    n = np.concatenate([zero, np.cumsum(valid, axis=-1)], axis=-1)
    out = np.full(x.shape, np.nan)
    if x.shape[-1] >= w:
        sums, counts = c[..., w:] - c[..., :-w], n[..., w:] - n[..., :-w]
        with np.errstate(invalid="ignore", divide="ignore"):
            out[..., w - 1:] = np.where(counts >= math.ceil(share * w), sums / np.maximum(counts, 1), np.nan)
    return out


def rolling_max(x: Array, w: int) -> Array:
    """Max over columns d − w + 1 .. d of the present values (NaN when none), by doubling: O(n log w)."""
    top = np.where(np.isfinite(x), x, -np.inf)
    span = 1
    while span * 2 <= w:
        shifted = np.full(top.shape, -np.inf)
        shifted[..., span:] = top[..., :-span]
        top = np.maximum(top, shifted)
        span *= 2
    rest = np.full(top.shape, -np.inf)
    if w - span > 0:
        rest[..., w - span:] = top[..., :top.shape[-1] - (w - span)]
    out = np.maximum(top, rest)
    out[..., :w - 1] = -np.inf
    return np.where(np.isfinite(out), out, np.nan)


def log_returns(close: Array) -> Array:
    with np.errstate(invalid="ignore", divide="ignore"):
        out: Array = np.log(close) - np.log(_shift(close, 1))
    return out


def ref_variance(close: Array) -> Array:
    """The reference daily variance as of each close: mean squared log return over ``REF_SESSIONS`` sessions, floored."""
    r = log_returns(close)
    out: Array = np.maximum(rolling_mean(r * r, REF_SESSIONS), VAR_FLOOR)
    return out


# ── features at chosen (stock, session) cells ────────────────────────────────────────────────────
def features(p: Panel, rows: Array, cols: Array) -> dict[str, Array]:
    """Every feature of ``NAMES`` for decisions on session ``cols[k]`` about stock ``rows[k]``: read at column
    ``cols[k] − 1`` (the previous close). Each full matrix is computed, sampled and dropped in turn (bounded memory)."""
    at = cols - 1
    ok = at >= 0
    a = np.where(ok, at, 0)

    def take(m: Array) -> Array:
        v = m[rows, a] if m.ndim == 2 else m[a]
        return np.where(ok, v, np.nan).astype(np.float32)

    out: dict[str, Array] = {}
    with np.errstate(invalid="ignore", divide="ignore"):
        logc = np.log(p.close)
        out["ret_5d"] = take(logc - _shift(logc, 5))
        out["ret_1m"] = take(logc - _shift(logc, 21))
        out["mom_12_1"] = take(_shift(logc, 21) - _shift(logc, 252))
        out["high_52w"] = take(logc - rolling_max(logc, 252))
        del logc
        r = log_returns(p.close)
        v = np.where(np.isfinite(r), np.maximum(r * r, VAR_FLOOR), np.nan)
        out["log_rv_day"] = take(np.log(v))
        week, month = rolling_mean(v, 5), rolling_mean(v, 22)
        out["log_rv_week"], out["log_rv_month"] = take(np.log(week)), take(np.log(month))
        ref = np.maximum(rolling_mean(r * r, REF_SESSIONS), VAR_FLOOR)
        out["log_sigma_ref"] = take(0.5 * np.log(ref))
        out["rv_ratio"] = take(np.log(week) - np.log(ref))
        del r, v, week, month, ref
        d20, d5, d63 = rolling_mean(p.dollars, 20), rolling_mean(p.dollars, 5), rolling_mean(p.dollars, 63)
        out["log_dollar_volume"] = take(np.log10(np.where(d20 > 0, d20, np.nan)))
        out["volume_surge"] = take(np.log(np.where((d5 > 0) & (d63 > 0), d5 / d63, np.nan)))
        del d20, d5, d63
        lm = np.log(p.market)
        out["mkt_ret_5d"] = take(lm - _shift(lm, 5))
        out["mkt_ret_1m"] = take(lm - _shift(lm, 21))
        rm = lm - _shift(lm, 1)
        out["mkt_log_rv_month"] = take(np.log(np.maximum(rolling_mean(rm * rm, 22), VAR_FLOOR)))
        ls = np.log(p.sector)
        out["sector_ret_1m"] = take(ls - _shift(ls, 21))
        out["rel_sector_1m"] = out["ret_1m"] - out["sector_ret_1m"]
        del ls
    for name in ("committee", "opt_iv_spread", "opt_skew", "opt_iv_rv", "earnings_days"):
        m = p.side.get(name)
        out[name] = take(m) if m is not None else np.full(len(rows), np.nan, np.float32)
    if "earnings_days" in out:                       # days are counted from the decision session, not the close before
        ordinals = np.asarray([d.toordinal() for d in p.days])
        gap = ordinals[np.clip(cols, 0, len(ordinals) - 1)] - ordinals[np.clip(cols - 1, 0, len(ordinals) - 1)]
        out["earnings_days"] = (out["earnings_days"] - gap).astype(np.float32)
    return out


def matrix(feats: dict[str, Array]) -> Array:
    return np.column_stack([feats[k] for k in NAMES]).astype(np.float32)


def standardized_returns(p: Panel, rows: Array, cols: Array, h: int) -> tuple[Array, Array]:
    """(z, scale): the log return from the close of session ``cols`` to ``h`` sessions later, divided by the reference
    scale as of the previous close × √h (NaN where either close is missing or beyond the panel)."""
    end = cols + h
    inside = (end < p.close.shape[1]) & (cols >= 1)
    e = np.where(inside, end, 0)
    c0 = p.close[rows, np.where(inside, cols, 0)]
    c1 = p.close[rows, e]
    ref = ref_variance(p.close)[rows, np.where(inside, cols - 1, 0)]
    scale = np.sqrt(ref * h)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where(inside, np.log(c1 / c0) / scale, np.nan)
    return z, np.where(inside, scale, np.nan)


__all__ = [
    "FRESH",
    "NAMES",
    "REF_SESSIONS",
    "VAR_FLOOR",
    "Panel",
    "candidates",
    "earnings_days",
    "features",
    "load",
    "log_returns",
    "matrix",
    "ref_variance",
    "rolling_max",
    "rolling_mean",
    "sector_funds",
    "side_data",
    "standardized_returns",
]
