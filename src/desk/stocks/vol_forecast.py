"""KESTREL's volatility forecast: how much it expects a stock to move, learned from its recent daily, weekly and
monthly swings plus what options imply (docs/STOCKS_METHOD.md → Volatility forecast).

Until 2026-11-18 KESTREL's forecast variance was a fixed blend, 0.7 × implied² + 0.3 × realized² (``IV_WEIGHT``). The
HAR model (Corsi 2009) and its implied-volatility extension forecast the variance that follows better than either input
alone, so the blend is replaced where, and only where, the learned forecast beat it out of sample.

* **Realized variance** per stock and finished session (``update``, table ``stk_rv_daily``, computed once and
  incrementally): with enough archived minute bars (``minute_archive``; at least ``MIN_SESSION_SHARE`` of the
  session), the sum of squared five-minute log returns from the session's first open plus the squared overnight
  return (previous close to that open), source ``minutes5``; otherwise the squared close-to-close log return, source
  ``daily``. Prices are split-adjusted; a newly recorded split recomputes that stock.
* **Implied variance**: the stock's 30-day at-the-money IV that day (the collected chain's IV30, else Massive's from
  real closing trades: ``iv_history``), as a per-session variance IV² / 252. A day without one has no HAR-IV row; its
  stock still feeds the HAR-RV model.
* **Model**: pooled OLS across stocks in log variance. For each horizon h in ``HORIZONS`` sessions the target is the
  log of the average realized variance over the next h sessions; the features are the log of the day's realized
  variance, of its 5-session and 22-session averages and (HAR-IV) of the implied variance, plus an intercept. Each day's
  variance is floored at ``VAR_FLOOR`` before the log. The log forecast is turned back into a variance with Duan's
  smearing factor (the mean of exp(residual) over the last ``SMEAR_SESSIONS`` sessions of training rows).
* **Walk-forward** (``walk_forward``): one fit on the first session of each week, from the rows whose target window
  ended before that session (so a fit never sees an outcome that was not yet known), expanding. The forecast made on
  day D uses the newest fit on or before D. Fits are stored append-only (``stk_vol_fits``) with their date and hash.
* **Evaluation and gate**: every forecast is scored out of sample against what followed: QLIKE (y/f − ln(y/f) − 1, the
  standard loss for variance forecasts) and the squared error of log variance, for HAR-IV, HAR-RV without IV, IV alone
  and the old blend, on the same rows. A horizon goes live (``Model.live``) only when, on forecasts realized before
  the fit day, at least ``GATE_SESSIONS`` sessions and ``GATE_STOCKS`` stocks of held-out forecasts show HAR-IV with a
  lower QLIKE *and* a lower log-variance error than the old blend; otherwise that horizon keeps the old blend. Days
  without an implied volatility use HAR-RV only where it beat realized volatility alone the same way.
* **Use** (``forecast_sigma``): annualized volatility and the method used. Between the fitted horizons the log
  variance is interpolated (both neighbours must be live); beyond 63 sessions it reverts to the stock's own 252-session
  average variance. A replay on a past day uses the newest fit on or before that day, else the old blend.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any, NamedTuple

import numpy as np

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex

Array = np.ndarray
NAME = "stocks_vol_forecast"
VERSION = 1
JOB = "vol_forecast"
TRADING_YEAR = 252
IV_WEIGHT = 0.7                  # the old blend, kept as the fallback: variance = 0.7 × implied² + 0.3 × realized²
HORIZONS = (1, 5, 10, 21, 63)    # sessions
WEEK, MONTH, YEAR = 5, 22, 252
RV_SESSIONS = 20                 # the old blend's realized volatility: 20 close-to-close returns
BUCKET_MINUTES = 5
MIN_SESSION_SHARE = 0.75         # minute bars needed for the five-minute measure, as a share of the session
MINUTE_CLOSE_MATCH = 0.02        # the last minute's close must be within 2% of the day's close, else minutes are not used
VAR_FLOOR = 1e-6                 # a 0.1% daily move: a flat day is not an infinitely calm one
HISTORY = 1000                   # most recent sessions computed per stock
MAX_STOCKS = 500                 # the stocks tracked: optioned, then by dollar volume
MIN_FIT_ROWS = 1000              # stock-sessions a fit needs
SMEAR_SESSIONS = 252
GATE_SESSIONS, GATE_STOCKS = 60, 30
UPGRADE_SESSIONS = 10            # a 'daily' row this recent is recomputed when its minute bars arrive later
SETTLE = timedelta(minutes=15)
UPDATE_SECONDS = 60.0            # a collection pass's budget for realized variance
ABOUT = ("How much KESTREL expects the stock to move, learned from its recent daily, weekly and monthly swings plus "
         "what options imply.")
METHODS = {"har_iv": "learned from the stock's recent swings plus what options imply",
           "har_rv": "learned from the stock's recent swings (no option price that day)",
           "blend": "fixed blend: 70% what options imply, 30% the last 20 sessions' swings"}
FEATURES = {"har_iv": ("intercept", "log_rv_day", "log_rv_week", "log_rv_month", "log_iv"),
            "har_rv": ("intercept", "log_rv_day", "log_rv_week", "log_rv_month")}


def content() -> dict[str, Any]:
    return {"version": VERSION, "horizons": list(HORIZONS), "week": WEEK, "month": MONTH, "year": YEAR,
            "rv": "minutes5 = overnight^2 + sum of 5-minute squared log returns if minutes >= share of session and the "
                  "last minute close matches the day's close; else daily = squared close-to-close log return",
            "bucket_minutes": BUCKET_MINUTES, "min_session_share": MIN_SESSION_SHARE,
            "minute_close_match": MINUTE_CLOSE_MATCH, "var_floor": VAR_FLOOR, "iv": "IV30^2/252, collected then massive",
            "features": {k: list(v) for k, v in FEATURES.items()},
            "target": "log mean realized variance over the next h sessions", "fit": "pooled OLS, expanding, first "
            "session of each week, rows whose target ended before the fit day", "bias": "Duan smearing",
            "smear_sessions": SMEAR_SESSIONS, "min_fit_rows": MIN_FIT_ROWS, "loss": ["qlike", "mse_log"],
            "gate": {"sessions": GATE_SESSIONS, "stocks": GATE_STOCKS, "beats": "old blend on qlike and mse_log"},
            "no_iv_gate": "har_rv beats realized volatility alone the same way",
            "beyond": "mean-revert to the 252-session average", "between": "log variance linear in h",
            "iv_weight": IV_WEIGHT, "rv_sessions": RV_SESSIONS, "history": HISTORY, "max_stocks": MAX_STOCKS}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


# ── realized variance of one session ─────────────────────────────────────────────────────────────
def intraday_variance(bars: Sequence[Any], d: date) -> tuple[float, float, float, int] | None:
    """(sum of squared five-minute log returns from the session's first open, that open, the last close, minutes
    used), or None when the regular session has fewer than ``MIN_SESSION_SHARE`` of its minutes."""
    from desk.stocks import calendar as cal

    start, close = 9 * 60 + 30, cal.close_time(d)
    end = close.hour * 60 + close.minute
    session = []
    for b in bars:
        local = cal.new_york(b.at)
        minute = local.hour * 60 + local.minute
        if local.date() == d and start <= minute < end and b.open > 0 and b.close > 0:
            session.append((minute, float(b.open), float(b.close)))
    if len(session) < MIN_SESSION_SHARE * (end - start):
        return None
    session.sort()
    buckets: dict[int, float] = {}
    for minute, _, c in session:
        buckets[(minute - start) // BUCKET_MINUTES] = c          # the bucket's last close
    prices = np.log([session[0][1], *(buckets[k] for k in sorted(buckets))])
    return float(np.sum(np.diff(prices) ** 2)), session[0][1], session[-1][2], len(session)


def session_variance(prev_close: float, close: float, minutes: tuple[float, float, float, int] | None,
                     factor: float = 1.0) -> tuple[float, float, str, int | None]:
    """(realized variance, close-to-close log return, source, minutes used). ``prev_close`` and ``close`` are
    split-adjusted; ``factor`` (adjusted ÷ raw price that day) puts the raw minute prices on the same basis."""
    ret = math.log(close / prev_close)
    if minutes is not None:
        intraday, first_open, last_close, n = minutes
        if abs(last_close * factor / close - 1) <= MINUTE_CLOSE_MATCH:
            overnight = math.log(first_open * factor / prev_close)
            return overnight * overnight + intraday, ret, "minutes5", n
    return ret * ret, ret, "daily", None


# ── the daily table ──────────────────────────────────────────────────────────────────────────────
def tracked(conn: sqlite3.Connection, today: date) -> list[str]:
    """The stocks with collected option chains or an IV history, by recent dollar volume, at most ``MAX_STOCKS``."""
    from desk.stocks.repo import day_of

    wanted = {r[0] for r in conn.execute("SELECT DISTINCT underlying FROM stk_option_daily")}
    wanted |= {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM stk_iv_daily")}
    if not wanted:
        return []
    volume = {r[0]: r[1] or 0.0 for r in conn.execute(
        "SELECT symbol, SUM(close*volume) FROM stk_bars WHERE day>=? GROUP BY symbol", (day_of(today - timedelta(days=42)),))
        if r[0] in wanted}
    return sorted(wanted, key=lambda s: (-volume.get(s, 0.0), s))[:MAX_STOCKS]


def _splits_key(rows: Iterable[tuple[Any, ...]]) -> str:
    return sha256_hex(canonical_json(sorted([list(r) for r in rows])))[:16]


def update_symbol(conn: sqlite3.Connection, symbol: str, last_closed: date, now: datetime) -> int:
    """Compute the stock's finished sessions not yet stored (and recent 'daily' rows whose minutes have since been
    archived); returns the rows written."""
    from desk.stocks import calendar as cal
    from desk.stocks import minute_archive
    from desk.stocks.hub import adjusted_bars
    from desk.stocks.repo import date_of, day_of

    splits = conn.execute("SELECT execution_date, split_from, split_to FROM stk_splits WHERE symbol=?",
                          (symbol,)).fetchall()
    key = _splits_key(tuple(r) for r in splits)
    meta = conn.execute("SELECT splits_key FROM stk_rv_symbols WHERE symbol=?", (symbol,)).fetchone()
    if meta is None or meta[0] != key:
        conn.execute("DELETE FROM stk_rv_daily WHERE symbol=?", (symbol,))
        conn.execute("INSERT OR REPLACE INTO stk_rv_symbols VALUES (?,?,?)", (symbol, key, to_iso(now)))
    last = conn.execute("SELECT MAX(day) FROM stk_rv_daily WHERE symbol=?", (symbol,)).fetchone()[0]
    end = day_of(last_closed)
    window = cal.trading_days(last_closed - timedelta(days=UPGRADE_SESSIONS * 2), last_closed) or [last_closed]
    recent = day_of(window[-min(len(window), UPGRADE_SESSIONS)])
    upgrade = {int(r[0]) for r in conn.execute(
        "SELECT r.day FROM stk_rv_daily r JOIN stk_minute_bars m ON m.symbol=r.symbol AND m.day=r.day "
        "WHERE r.symbol=? AND r.source='daily' AND r.day>=?", (symbol, recent))}
    if last is None:
        row = conn.execute("SELECT day FROM stk_bars WHERE symbol=? AND day<=? ORDER BY day DESC LIMIT 1 OFFSET ?",
                           (symbol, end, HISTORY)).fetchone()
        lower = int(row[0]) if row else 0
    else:
        lower = min([int(last), *upgrade]) - 10
        if int(last) >= end and not upgrade:
            return 0
    raw = [dict(r) for r in conn.execute("SELECT day, open, close FROM stk_bars WHERE symbol=? AND day>=? AND day<=? "
                                         "ORDER BY day", (symbol, lower, end))]
    raw = [r for r in raw if r["close"] and r["close"] > 0]
    if len(raw) < 2:
        return 0
    adjusted = adjusted_bars(conn, symbol, [dict(r) for r in raw])
    with_minutes = {int(r[0]) for r in conn.execute("SELECT day FROM stk_minute_bars WHERE symbol=? AND day>=? AND "
                                                    "day<=?", (symbol, lower, end))}
    out = []
    for (p, a), (r, b) in pairwise(zip(raw, adjusted, strict=True)):
        day = int(r["day"])
        if (last is not None and day <= int(last) and day not in upgrade) or \
                cal.previous_trading_day(date_of(day)) != date_of(int(p["day"])):
            continue
        minutes = None
        if day in with_minutes:
            minutes = intraday_variance(minute_archive.read(conn, symbol, date_of(day)), date_of(day))
        rv, ret, source, n = session_variance(float(a["close"]), float(b["close"]), minutes,
                                              float(b["close"]) / float(r["close"]))
        if source == "daily" and day in upgrade:
            continue                                              # its minutes still do not qualify: keep the row
        out.append((symbol, day, rv, ret, source, n, to_iso(now)))
    conn.executemany("INSERT OR REPLACE INTO stk_rv_daily VALUES (?,?,?,?,?,?,?)", out)
    return len(out)


def update(conn: sqlite3.Connection, now: datetime, *, budget_seconds: float | None = UPDATE_SECONDS,
           progress: Callable[[str], None] | None = None, symbols: Sequence[str] | None = None) -> dict[str, Any]:
    """Every tracked stock's new finished sessions, within ``budget_seconds`` (the rest next pass: ``remaining``)."""
    from desk.stocks import calendar as cal
    from desk.storage.db import transaction

    last_closed = cal.last_closed_session(now, SETTLE)
    wanted = list(symbols) if symbols is not None else tracked(conn, last_closed)
    started, rows, remaining = time.monotonic(), 0, 0
    for i, symbol in enumerate(wanted):
        if budget_seconds is not None and time.monotonic() - started > budget_seconds:
            remaining = len(wanted) - i
            break
        with transaction(conn):
            rows += update_symbol(conn, symbol, last_closed, now)
        if progress and (i + 1) % 25 == 0:
            progress(f"realized variance: {i + 1} of {len(wanted)} stocks")
    sources = dict(conn.execute("SELECT source, COUNT(*) FROM stk_rv_daily GROUP BY source").fetchall())
    return {"stocks": len(wanted), "rows_written": rows, "remaining": remaining, "through": last_closed.isoformat(),
            "rows_by_source": {k: int(v) for k, v in sources.items()}}


# ── the panel: stocks × sessions ─────────────────────────────────────────────────────────────────
@dataclass
class Panel:
    """Per-session realized variance, close-to-close log return and implied variance (IV² / 252), stocks × sessions
    (NaN where missing). ``days`` are consecutive trading sessions."""
    symbols: list[str]
    days: list[date]
    rv: Array
    ret: Array
    iv: Array


def load_panel(conn: sqlite3.Connection, symbols: Sequence[str]) -> Panel:
    from desk.stocks import calendar as cal
    from desk.stocks.repo import date_of

    wanted = list(symbols)
    if not wanted:
        return Panel([], [], np.zeros((0, 0)), np.zeros((0, 0)), np.zeros((0, 0)))
    index = {s: i for i, s in enumerate(wanted)}
    rows = [r for r in conn.execute("SELECT symbol, day, rv, ret FROM stk_rv_daily ORDER BY day") if r[0] in index]
    if not rows:
        return Panel(wanted, [], np.zeros((len(wanted), 0)), np.zeros((len(wanted), 0)), np.zeros((len(wanted), 0)))
    days = cal.trading_days(date_of(rows[0][1]), date_of(rows[-1][1]))
    col = {d.toordinal(): j for j, d in enumerate(days)}
    shape = (len(wanted), len(days))
    rv, ret, iv = np.full(shape, np.nan), np.full(shape, np.nan), np.full(shape, np.nan)
    for symbol, day, v, r in rows:
        if (j := col.get(int(day))) is not None:
            rv[index[symbol], j], ret[index[symbol], j] = v, r
    first, last = days[0].toordinal(), days[-1].toordinal()
    for symbol, day, value in conn.execute("SELECT symbol, day, iv30 FROM stk_iv_daily WHERE day>=? AND day<=?",
                                           (first, last)):
        if symbol in index and (j := col.get(int(day))) is not None and value and value > 0:
            iv[index[symbol], j] = value * value / TRADING_YEAR
    for symbol, day, value in conn.execute(           # the collected chain's own IV30 wins over Massive's
            "SELECT underlying, day, json_extract(summary_json, '$.iv30') FROM stk_option_daily WHERE day>=? AND day<=?",
            (first, last)):
        if symbol in index and (j := col.get(int(day))) is not None and value and float(value) > 0:
            iv[index[symbol], j] = float(value) ** 2 / TRADING_YEAR
    return Panel(wanted, days, rv, ret, iv)


def trailing_mean(x: Array, w: int) -> Array:
    """Mean over sessions t−w+1..t along axis 1; NaN unless all w are present."""
    valid = np.isfinite(x)
    zero = np.zeros((x.shape[0], 1))
    c = np.concatenate([zero, np.cumsum(np.where(valid, x, 0.0), axis=1)], axis=1)
    n = np.concatenate([zero, np.cumsum(valid, axis=1)], axis=1)
    out = np.full(x.shape, np.nan)
    if x.shape[1] >= w:
        sums, counts = c[:, w:] - c[:, :-w], n[:, w:] - n[:, :-w]
        out[:, w - 1:] = np.where(counts == w, sums / w, np.nan)
    return out


def is_fit_day(d: date) -> bool:
    """The first session of its week."""
    from desk.stocks import calendar as cal

    return cal.previous_trading_day(d).isocalendar()[:2] != d.isocalendar()[:2]


# ── the model ────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class State:
    """One stock's recent realized variance as of ``day`` (per session, each day floored at ``VAR_FLOOR``)."""
    day: date
    d: float
    w: float
    m: float
    long: float | None = None          # its 252-session average (with at least 63 sessions), for beyond 63 sessions

    def features(self) -> tuple[float, float, float]:
        return math.log(self.d), math.log(self.w), math.log(self.m)


def state_from(days: Sequence[date], rvs: Sequence[float], asof: date) -> State | None:
    """The state from a stock's rows up to ``asof`` (oldest first): its last 22 sessions must be consecutive and the
    newest at most three sessions before ``asof``."""
    from desk.stocks import calendar as cal

    k = bisect_right(days, asof)
    days, rvs = days[:k], rvs[:k]
    if len(days) < MONTH or len(cal.trading_days(days[-1], asof)) > 4:
        return None
    window = days[-MONTH:]
    if any(cal.previous_trading_day(b) != a for a, b in pairwise(window)):
        return None
    v = [max(float(x), VAR_FLOOR) for x in rvs[-YEAR:]]
    return State(days[-1], v[-1], sum(v[-WEEK:]) / WEEK, sum(v[-MONTH:]) / MONTH,
                 sum(v) / len(v) if len(v) >= 63 else None)


def state(conn: sqlite3.Connection, symbol: str, asof: date) -> State | None:
    from desk.stocks.repo import date_of, day_of

    try:
        rows = conn.execute("SELECT day, rv FROM stk_rv_daily WHERE symbol=? AND day<=? ORDER BY day DESC LIMIT ?",
                            (symbol, day_of(asof), YEAR)).fetchall()[::-1]
    except sqlite3.OperationalError:            # a database from before the volatility forecast
        return None
    return state_from([date_of(r[0]) for r in rows], [r[1] for r in rows], asof)


@dataclass(frozen=True)
class Coef:
    beta: tuple[float, ...]
    smear: float
    rows: int


@dataclass(frozen=True)
class Model:
    """One fit: per horizon and kind ("har_iv", "har_rv") its coefficients, and the gate as of its fit day."""
    fit_day: date
    sha: str
    coef: dict[str, dict[int, Coef]]
    live: dict[int, bool]               # HAR-IV used at this horizon (it beat the old blend out of sample)
    live_no_iv: dict[int, bool]         # HAR-RV used when there is no implied volatility (it beat realized alone)

    @classmethod
    def from_json(cls, fit_day: date, sha: str, value: dict[str, Any]) -> Model:
        coef = {kind: {int(h): Coef(tuple(c["beta"]), float(c["smear"]), int(c["rows"]))
                       for h, c in (value.get("coef", {}).get(kind) or {}).items() if c}
                for kind in FEATURES}
        return cls(fit_day, sha, coef, {int(h): bool(v) for h, v in (value.get("live") or {}).items()},
                   {int(h): bool(v) for h, v in (value.get("live_no_iv") or {}).items()})

    def as_json(self) -> dict[str, Any]:
        return {"coef": {kind: {str(h): {"beta": [round(b, 10) for b in c.beta], "smear": round(c.smear, 10),
                                         "rows": c.rows} for h, c in sorted(by.items())}
                         for kind, by in self.coef.items()},
                "live": {str(h): v for h, v in sorted(self.live.items())},
                "live_no_iv": {str(h): v for h, v in sorted(self.live_no_iv.items())}}

    def _log_at(self, kind: str, h: int, s: State, iv: Array | None) -> Array | None:
        c = self.coef.get(kind, {}).get(h)
        if c is None or not (self.live if kind == "har_iv" else self.live_no_iv).get(h):
            return None
        d, w, m = s.features()
        x = c.beta[0] + c.beta[1] * d + c.beta[2] * w + c.beta[3] * m
        if kind == "har_iv":
            assert iv is not None
            out: Array = x + c.beta[4] * np.log(iv * iv / TRADING_YEAR) + math.log(c.smear)
            return out
        return np.asarray(x + math.log(c.smear))

    def variances(self, s: State, iv: Array | None, sessions: int) -> tuple[Array | None, str]:
        """Average per-session variance over the next ``sessions`` (None where this horizon is not live)."""
        kind = "har_iv" if iv is not None else "har_rv"
        h = max(1, int(sessions))
        top = HORIZONS[-1]
        if h >= top:
            lv = self._log_at(kind, top, s, iv)
            if lv is None:
                return None, "blend"
            v63 = np.exp(lv)
            long = s.long if s.long is not None else v63
            return (top * v63 + (h - top) * long) / h, kind
        lo = max(x for x in HORIZONS if x <= h)
        hi = min(x for x in HORIZONS if x >= h)
        a, b = self._log_at(kind, lo, s, iv), self._log_at(kind, hi, s, iv)
        if a is None or b is None:
            return None, "blend"
        mix = 0.0 if hi == lo else (h - lo) / (hi - lo)
        return np.exp((1 - mix) * a + mix * b), kind

    def methods(self) -> dict[str, str]:
        """The method live at each fitted horizon, with an implied volatility."""
        return {str(h): "har_iv" if self.live.get(h) and h in self.coef.get("har_iv", {}) else "blend"
                for h in HORIZONS}


@dataclass(frozen=True)
class Vol:
    """What the forecast needs for one stock on one decision day."""
    state: State | None
    model: Model | None


class Sigma(NamedTuple):
    sigma: float | None
    method: str


def blend(iv: float | None, rv: float | None) -> float | None:
    """The old fixed blend: annual volatility from 0.7 × implied² + 0.3 × realized² (either alone when one is missing)."""
    if iv and rv:
        return math.sqrt(IV_WEIGHT * iv * iv + (1 - IV_WEIGHT) * rv * rv)
    return iv or rv


def forecast_sigmas(iv: Array, rv: float | None, *, t: float, vol: Vol | None) -> tuple[Array, str]:
    """Annual volatility for contracts of one expiry ``t`` years out (each with its own implied volatility), and the
    method: the learned forecast where its horizon is live, else the old blend."""
    iv = np.asarray(iv, dtype=float)
    old: Array = np.sqrt(IV_WEIGHT * iv ** 2 + (1 - IV_WEIGHT) * rv ** 2) if rv else iv
    if vol is None or vol.model is None or vol.state is None or t <= 0:
        return old, "blend"
    v, method = vol.model.variances(vol.state, np.maximum(iv, 1e-4), round(t * TRADING_YEAR))
    if v is None:
        return old, "blend"
    out: Array = np.broadcast_to(np.sqrt(np.asarray(v) * TRADING_YEAR), iv.shape).astype(float)
    return out, method


def forecast_sigma(iv: float | None, rv: float | None, *, t: float, vol: Vol | None = None) -> Sigma:
    """KESTREL's annual volatility for a horizon of ``t`` years and the method used (``METHODS``). Without an implied
    volatility the HAR-RV forecast is used where live, else realized volatility alone."""
    if iv:
        sigmas, method = forecast_sigmas(np.array([iv]), rv, t=t, vol=vol)
        return Sigma(float(sigmas[0]), method)
    if vol is not None and vol.model is not None and vol.state is not None and t > 0:
        v, method = vol.model.variances(vol.state, None, round(t * TRADING_YEAR))
        if v is not None:
            return Sigma(float(math.sqrt(float(np.asarray(v)) * TRADING_YEAR)), method)
    return Sigma(blend(iv, rv), "blend")


# ── walk-forward fit and evaluation ──────────────────────────────────────────────────────────────
@dataclass
class Fit:
    day: date
    model: Model
    evaluation: dict[str, Any]
    rows: int
    stocks: int

    def sha(self) -> str:
        return self.model.sha


def _qlike(y: Array, f: Array) -> Array:
    ratio = y / f
    out: Array = ratio - np.log(ratio) - 1
    return out


def _solve(xtx: Array, xty: Array) -> Array | None:
    ridge = np.eye(len(xty)) * 1e-9 * max(1.0, float(np.trace(xtx)))
    ridge[0, 0] = 0.0
    try:
        beta = np.linalg.solve(xtx + ridge, xty)
    except np.linalg.LinAlgError:
        return None
    return beta if np.all(np.isfinite(beta)) else None


def walk_forward(panel: Panel, *, progress: Callable[[str], None] | None = None) -> list[Fit]:
    """Every weekly fit across the panel, each from targets realized before its fit day, with the out-of-sample
    evaluation (and gate) realized before it. Pure: the same panel gives the same fits."""
    rv, days = panel.rv, panel.days
    n_stocks, n_days = rv.shape
    if n_days < MONTH + 2 or n_stocks == 0:
        return []
    v = np.where(np.isfinite(rv), np.maximum(rv, VAR_FLOOR), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        feats = [np.log(v), np.log(trailing_mean(v, WEEK)), np.log(trailing_mean(v, MONTH))]
        log_iv = np.log(panel.iv)
        r_mean, r2_mean = trailing_mean(panel.ret, RV_SESSIONS), trailing_mean(panel.ret ** 2, RV_SESSIONS)
    rv20 = np.maximum(r2_mean - r_mean ** 2, VAR_FLOOR)            # the old blend's realized variance per session
    blend_v = IV_WEIGHT * panel.iv + (1 - IV_WEIGHT) * rv20
    base = np.stack([np.ones_like(v), *feats], axis=-1)            # stocks × sessions × 4
    xs = {"har_rv": base, "har_iv": np.concatenate([base, log_iv[..., None]], axis=-1)}
    fit_days = [j for j in range(n_days) if is_fit_day(days[j])]
    fit_of = np.full(n_days, -1)
    for j in fit_days:
        fit_of[j:] = j
    coefs: dict[int, dict[str, dict[int, Coef]]] = defaultdict(lambda: defaultdict(dict))
    evaluations: dict[int, dict[str, Any]] = defaultdict(dict)
    gates: dict[int, dict[int, tuple[bool, bool]]] = defaultdict(dict)
    rows_used: dict[int, int] = defaultdict(int)
    for h in HORIZONS:
        if progress:
            progress(f"fitting {h}-session horizon")
        with np.errstate(invalid="ignore", divide="ignore"):
            future = np.full(v.shape, np.nan)
            if n_days > h:
                future[:, :-h] = trailing_mean(v, h)[:, h:]
            y = np.log(future)
        preds: dict[str, Array] = {}
        for kind, x in xs.items():
            mask = np.isfinite(y) & np.all(np.isfinite(x), axis=-1)
            xm = np.where(mask[..., None], x, 0.0)
            ym = np.where(mask, y, 0.0)
            cum_xtx = np.cumsum(np.einsum("sti,stj->tij", xm, xm), axis=0)
            cum_xty = np.cumsum(np.einsum("sti,st->ti", xm, ym), axis=0)
            cum_n = np.cumsum(mask.sum(axis=0))
            pred = np.full(v.shape, np.nan)
            for f in fit_days:
                last = f - 1 - h                                   # targets end on session f-1 at the latest
                if last < 0 or cum_n[last] < MIN_FIT_ROWS:
                    continue
                beta = _solve(cum_xtx[last], cum_xty[last])
                if beta is None:
                    continue
                lo = max(0, last - SMEAR_SESSIONS + 1)
                window = mask[:, lo:last + 1]
                resid = (y[:, lo:last + 1] - np.einsum("sti,i->st", x[:, lo:last + 1], beta))[window]
                smear = float(np.mean(np.exp(resid))) if resid.size else 1.0
                coefs[f][kind][h] = Coef(tuple(float(b) for b in beta), smear, int(cum_n[last]))
                rows_used[f] = max(rows_used[f], int(cum_n[last]))
                upto = min(n_days, next((g for g in fit_days if g > f), n_days))
                with np.errstate(invalid="ignore", over="ignore"):
                    pred[:, f:upto] = np.exp(np.einsum("sti,i->st", x[:, f:upto], beta)) * smear
            preds[kind] = pred
        target = future
        with np.errstate(invalid="ignore", divide="ignore"):
            common = (np.isfinite(target) & np.isfinite(preds["har_iv"]) & np.isfinite(preds["har_rv"])
                      & np.isfinite(blend_v) & (target > 0))
            no_iv = np.isfinite(target) & np.isfinite(preds["har_rv"]) & np.isfinite(rv20) & (target > 0)
            losses = {}
            for name, f_ in (("har_iv", preds["har_iv"]), ("har_rv", preds["har_rv"]), ("blend", blend_v),
                             ("iv", panel.iv)):
                q = np.where(common, _qlike(target, f_), 0.0)
                e = np.where(common, (np.log(target) - np.log(f_)) ** 2, 0.0)
                losses[name] = (np.cumsum(np.nansum(q, axis=0)), np.cumsum(np.nansum(e, axis=0)))
            losses_no_iv = {}
            for name, f_ in (("har_rv", preds["har_rv"]), ("rv20", rv20)):
                q = np.where(no_iv, _qlike(target, f_), 0.0)
                e = np.where(no_iv, (np.log(target) - np.log(f_)) ** 2, 0.0)
                losses_no_iv[name] = (np.cumsum(np.nansum(q, axis=0)), np.cumsum(np.nansum(e, axis=0)))
        counts = _counts(common), _counts(no_iv)
        for f in fit_days:
            last = f - 1 - h
            if last < 0:
                continue
            main = _score(losses, counts[0], last, "har_iv", "blend")
            spare = _score(losses_no_iv, counts[1], last, "har_rv", "rv20")
            evaluations[f][str(h)] = {**main, "no_iv": spare}
            fitted = coefs.get(f, {})
            gates[f][h] = (main["live"] and h in fitted.get("har_iv", {}), spare["live"] and h in fitted.get("har_rv", {}))
    out = []
    for f in fit_days:
        if not any(coefs.get(f, {}).values()):
            continue
        live = {h: gates[f].get(h, (False, False))[0] for h in HORIZONS}
        live_no_iv = {h: gates[f].get(h, (False, False))[1] for h in HORIZONS}
        coef = {kind: dict(coefs[f].get(kind, {})) for kind in FEATURES}
        draft = Model(days[f], "", coef, live, live_no_iv)
        sha = sha256_hex(canonical_json({"version": VERSION, "fit_day": days[f].isoformat(), **draft.as_json()}))[:16]
        stocks = int(np.sum(np.any(np.isfinite(rv[:, :f]), axis=1)))
        out.append(Fit(days[f], Model(days[f], sha, coef, live, live_no_iv), evaluations[f], rows_used[f], stocks))
    return out


def _counts(mask: Array) -> tuple[Array, Array, Array]:
    """Cumulative over sessions: held-out rows, sessions with any, and stocks seen so far."""
    per = mask.sum(axis=0)
    first = np.where(mask.any(axis=1), np.argmax(mask, axis=1), mask.shape[1])
    stocks = np.cumsum(np.bincount(first, minlength=mask.shape[1] + 1)[:mask.shape[1]])
    return np.cumsum(per), np.cumsum(per > 0), stocks


def _score(losses: dict[str, tuple[Array, Array]], counts: tuple[Array, Array, Array], last: int, mine: str,
           old: str) -> dict[str, Any]:
    """Average losses of forecasts made on sessions up to ``last`` (so realized before the fit day), and the gate."""
    rows, sessions, stocks = (int(c[last]) for c in counts)
    if not rows:
        return {"rows": 0, "sessions": 0, "stocks": 0, "qlike": {}, "mse_log": {}, "live": False,
                "reduction_qlike": None, "reduction_mse_log": None}
    qlike = {k: float(q[last]) / rows for k, (q, _) in losses.items()}
    mse = {k: float(e[last]) / rows for k, (_, e) in losses.items()}
    enough = sessions >= GATE_SESSIONS and stocks >= GATE_STOCKS
    beats = qlike[mine] < qlike[old] and mse[mine] < mse[old]
    return {"rows": rows, "sessions": sessions, "stocks": stocks,
            "qlike": {k: round(x, 6) for k, x in qlike.items()}, "mse_log": {k: round(x, 6) for k, x in mse.items()},
            "live": bool(enough and beats), "enough": enough, "beats": beats,
            "reduction_qlike": round(1 - qlike[mine] / qlike[old], 4) if qlike[old] > 0 else None,
            "reduction_mse_log": round(1 - mse[mine] / mse[old], 4) if mse[old] > 0 else None}


# ── stored fits ──────────────────────────────────────────────────────────────────────────────────
def save_fits(conn: sqlite3.Connection, fits: Sequence[Fit], now: datetime) -> int:
    """Append fits for fit days not yet stored (a stored fit day is never rewritten)."""
    from desk.storage.db import transaction

    have = {int(r[0]) for r in conn.execute("SELECT fit_day FROM stk_vol_fits WHERE version=?", (VERSION,))}
    new = [f for f in fits if f.day.toordinal() not in have]
    with transaction(conn):
        conn.executemany("INSERT INTO stk_vol_fits VALUES (?,?,?,?,?,?,?,?)",
                         [(VERSION, f.day.toordinal(), f.model.sha, json.dumps(f.model.as_json(), allow_nan=False),
                           json.dumps(f.evaluation, allow_nan=False), f.rows, f.stocks, to_iso(now)) for f in new])
    return len(new)


def model_on(conn: sqlite3.Connection, d: date) -> Model | None:
    """The newest stored fit on or before ``d`` (it used only targets realized before its own fit day)."""
    try:
        row = conn.execute("SELECT fit_day, sha, model_json FROM stk_vol_fits WHERE version=? AND fit_day<=? "
                           "ORDER BY fit_day DESC LIMIT 1", (VERSION, d.toordinal())).fetchone()
    except sqlite3.OperationalError:            # a database from before the volatility forecast
        return None
    return None if row is None else Model.from_json(date.fromordinal(row[0]), row[1], json.loads(row[2]))


def latest(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The newest stored fit with its evaluation, for reports."""
    try:
        row = conn.execute("SELECT fit_day, sha, model_json, evaluation_json, rows, stocks, fitted_at FROM stk_vol_fits "
                           "WHERE version=? ORDER BY fit_day DESC LIMIT 1", (VERSION,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    model = Model.from_json(date.fromordinal(row[0]), row[1], json.loads(row[2]))
    return {"fit_day": model.fit_day.isoformat(), "sha": row[1], "model": model, "evaluation": json.loads(row[3]),
            "rows": int(row[4]), "stocks": int(row[5]), "fitted_at": row[6], "methods": model.methods()}


class Lookup:
    """Fits and realized variance read once, for a replay asking about many stocks and days."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._fits: list[tuple[int, str, str]] | None = None
        self._models: dict[int, Model] = {}
        self._rows: dict[str, tuple[list[date], list[float]]] = {}

    def model(self, d: date) -> Model | None:
        if self._fits is None:
            try:
                self._fits = [(int(r[0]), r[1], r[2]) for r in self.conn.execute(
                    "SELECT fit_day, sha, model_json FROM stk_vol_fits WHERE version=? ORDER BY fit_day", (VERSION,))]
            except sqlite3.OperationalError:
                self._fits = []
        k = bisect_right([f[0] for f in self._fits], d.toordinal())
        if not k:
            return None
        day, sha, text = self._fits[k - 1]
        if day not in self._models:
            self._models[day] = Model.from_json(date.fromordinal(day), sha, json.loads(text))
        return self._models[day]

    def vol(self, symbol: str, d: date) -> Vol:
        if symbol not in self._rows:
            try:
                rows = self.conn.execute("SELECT day, rv FROM stk_rv_daily WHERE symbol=? ORDER BY day",
                                         (symbol,)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            self._rows[symbol] = ([date.fromordinal(r[0]) for r in rows], [float(r[1]) for r in rows])
        days, rvs = self._rows[symbol]
        return Vol(state_from(days, rvs, d), self.model(d))


# ── the job ──────────────────────────────────────────────────────────────────────────────────────
def refit(conn: sqlite3.Connection, now: datetime, *, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Rebuild the walk-forward from the stored rows and append the fit days not yet stored."""
    from desk.stocks import calendar as cal

    symbols = tracked(conn, cal.last_closed_session(now, SETTLE))
    if progress:
        progress(f"loading {len(symbols)} stocks' realized and implied variance")
    panel = load_panel(conn, symbols)
    fits = walk_forward(panel, progress=progress)
    added = save_fits(conn, fits, now)
    newest = fits[-1] if fits else None
    return {"fits": len(fits), "added": added, "sessions": len(panel.days),
            "newest_fit": None if newest is None else newest.day.isoformat(),
            "methods": None if newest is None else newest.model.methods()}


def current_fit_day(now: datetime) -> date:
    """The newest fit day whose data is in: the first session of the latest closed session's week."""
    from desk.stocks import calendar as cal

    d = cal.last_closed_session(now, SETTLE)
    while not is_fit_day(d):
        d = cal.previous_trading_day(d)
    return d


def refit_due(conn: sqlite3.Connection, now: datetime, detail: dict[str, Any]) -> bool:
    """At most weekly: once the week's fit day is in and no fit (nor attempt) exists for it."""
    target = current_fit_day(now)
    if detail.get("refit_for") == target.isoformat():
        return False
    row = conn.execute("SELECT MAX(fit_day) FROM stk_vol_fits WHERE version=?", (VERSION,)).fetchone()
    return row[0] is None or int(row[0]) < target.toordinal()


def step(conn: sqlite3.Connection, now: datetime, detail: dict[str, Any], *,
         progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """One collection pass: realized variance within its budget, then the weekly refit once every stock is current."""
    out: dict[str, Any] = update(conn, now, progress=progress)
    out["refit_for"] = detail.get("refit_for")
    if not out["remaining"] and refit_due(conn, now, detail):
        out["refit"] = refit(conn, now, progress=progress)
        out["refit_for"] = current_fit_day(now).isoformat()
    return out


async def job(market: Any, now: datetime) -> dict[str, Any]:
    """The collection worker's step (``runner.cycle``), recorded as the ``vol_forecast`` job."""
    previous = (market.jobs().get(JOB) or {}).get("detail") or {}
    try:
        out = step(market.conn, now, previous)
    except (sqlite3.Error, ArithmeticError, ValueError) as exc:     # recorded; never stops collection
        error = f"{type(exc).__name__}: {str(exc)[:240]}"
        market.job(JOB, now, error=error)
        return {"ok": False, "error": error}
    market.job(JOB, now, detail=out)
    return {"ok": True, **out}


def summary_lines(conn: sqlite3.Connection) -> list[str]:
    """Plain lines for ``desk stocks vol-forecast``."""
    info = latest(conn)
    if info is None:
        return ["No volatility fit yet: it needs at least " f"{MIN_FIT_ROWS} stock-sessions of realized variance; "
                "every Plays chance uses the fixed blend meanwhile."]
    lines = [f"Newest fit {info['fit_day']} ({info['sha']}), {info['rows']:,} stock-sessions, {info['stocks']} stocks.",
             ABOUT, "Out of sample, forecasts realized before the fit day (QLIKE; lower is better):"]
    for h in HORIZONS:
        e = info["evaluation"].get(str(h)) or {}
        q = e.get("qlike") or {}
        method = info["methods"].get(str(h), "blend")
        if not e.get("rows"):
            lines.append(f"  {h:>2} sessions: no held-out forecasts yet → {method}")
            continue
        nan = float("nan")
        lines.append(f"  {h:>2} sessions: HAR-IV {q.get('har_iv', nan):.4f}, HAR-RV {q.get('har_rv', nan):.4f}, "
                     f"IV alone {q.get('iv', nan):.4f}, old blend {q.get('blend', nan):.4f} "
                     f"({e['rows']:,} forecasts, {e['sessions']} sessions, {e['stocks']} stocks) → {method}")
    lines.append(f"A horizon goes live only with at least {GATE_SESSIONS} sessions and {GATE_STOCKS} stocks of held-out "
                 "forecasts and a lower QLIKE and log-variance error than the old blend.")
    return lines


__all__ = ["ABOUT", "HORIZONS", "IV_WEIGHT", "JOB", "METHODS", "NAME", "VERSION", "Coef", "Fit", "Lookup", "Model",
           "Panel", "Sigma", "State", "Vol", "blend", "content", "forecast_sigma", "forecast_sigmas", "intraday_variance",
           "job", "latest", "load_panel", "model_on", "refit", "refit_due", "save_fits", "session_variance", "state",
           "state_from", "step", "summary_lines", "tracked", "trailing_mean", "update", "update_symbol", "version_hash",
           "walk_forward"]
