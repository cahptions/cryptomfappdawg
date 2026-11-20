"""The volatility-surprise model: how far the stock's realized volatility over an option's life will differ from what
its options imply, beyond what the HAR-IV forecast already says (docs/STOCKS_METHOD.md → Direction and volatility).

**Target.** For a stock on session t and horizon h ∈ ``HORIZONS`` sessions: y = log(mean realized variance over
t+1..t+h ÷ implied variance on t), with realized variance per session from ``vol_forecast`` (five-minute returns where
minute bars exist, else the squared daily return) and implied variance IV30² / 252 (the collected chain's IV30, else
Massive's). Goyal & Saretto (2009): the gap between realized and implied volatility predicts delta-hedged option
returns, so this is the quantity an option's price is wrong about.

**Baseline: HAR-IV alone.** The HAR-IV fit stored for that day (``vol_forecast``'s newest weekly fit on or before t,
whichever horizons it made live) gives its own log forecast of the same ratio, b = log(HAR-IV variance ÷ implied
variance) (before its smearing factor, as the target is a log). A day before the first HAR fit has no row.

**Model.** Gradient-boosted trees (squared error) on: the implied level, the implied/realized gap (log IV² against the
22-session realized variance), the HAR inputs (log realized variance over a day, a week, a month), b itself, the term
slope (IV90 − IV30), the OTM put skew and the call−put IV spread (``stk_option_signals``, newest within five sessions)
and the days to the next results and whether they fall inside the window (``panel.earnings_days``). Every input is
known at t's close. Every ``STRIDE``-th session per stock; at most ``MAX_ROWS`` rows a fit.

**Walk-forward, weekly, gated.** A fit on day F trains on rows whose window ended before F. Its evaluation: the
``EVAL_BLOCKS`` blocks of ``BLOCK`` sessions before F, each predicted by models trained only on windows that ended
before the block began. A horizon is live only with ``GATE_ROWS`` held-out rows over ``GATE_SESSIONS`` sessions and
``GATE_STOCKS`` stocks and a lower squared error than b with a Newey–West t of at least ``GATE_T``. Otherwise the
correction is zero: the HAR-IV forecast (or the blend where that is not live) is used as before.

**Use.** The predicted correction c = ŷ − b multiplies KESTREL's forecast variance by e^c (its volatility by e^(c/2),
bounded to ``BOUNDS``); between fitted horizons c is linear in h; beyond 63 sessions it fades as 63/h (the HAR forecast
itself reverts to the long-run variance there).
"""

from __future__ import annotations

import math
import sqlite3
import time
from bisect import bisect_left
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks import vol_forecast as vf
from desk.stocks.forecast import panel as pn
from desk.stocks.forecast import store
from desk.stocks.forecast.direction import newey_west_t, peak_mb

Array = np.ndarray
NAME = "stocks_vol_surprise"
VERSION = 2
MODEL = "vol_surprise"
HORIZONS = vf.HORIZONS
NAMES = ("log_iv", "iv_rv_gap", "har_b", "log_rv_day", "log_rv_week", "log_rv_month", "term_slope", "skew",
         "iv_spread", "earnings_days", "earnings_inside")
STRIDE = 3
MAX_ROWS = 120_000
EVAL_BLOCKS = 4
BLOCK = 63
MIN_TRAIN = 2_000
GATE_ROWS, GATE_SESSIONS, GATE_STOCKS = 2_000, 60, 30
GATE_T = 2.0
BOUNDS = (0.5, 2.0)                  # the volatility multiplier's range
THREADS = 1                      # one thread: a laptop shares its cores (OpenMP threads contend badly)
PARAMS = {"max_iter": 120, "learning_rate": 0.06, "max_leaf_nodes": 15, "min_samples_leaf": 100,
          "l2_regularization": 1.0, "early_stopping": False, "random_state": 13}


def content() -> dict[str, Any]:
    return {"version": VERSION, "horizons": list(HORIZONS), "features": list(NAMES),
            "availability": "option signals and complete earnings calendar receipts known at each close",
            "target": "log(mean realized variance over t+1..t+h / (IV30^2/252) on t)",
            "baseline": "HAR-IV log forecast of the same ratio from vol_forecast's newest fit on or before t (no smear)",
            "vol_forecast": [vf.VERSION, vf.version_hash()], "stride": STRIDE, "max_rows": MAX_ROWS,
            "eval_blocks": EVAL_BLOCKS, "block": BLOCK, "min_train": MIN_TRAIN, "params": PARAMS,
            "gate": {"rows": GATE_ROWS, "sessions": GATE_SESSIONS, "stocks": GATE_STOCKS, "t": GATE_T,
                     "loss": "squared error of the log ratio, Newey-West t across sessions"},
            "use": "variance x exp(prediction - baseline); volatility multiplier bounded", "bounds": list(BOUNDS),
            "beyond": "fades as 63/h past 63 sessions", "fresh": pn.FRESH}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


# ── the HAR-IV baseline, as it was stored ────────────────────────────────────────────────────────
def har_baseline(fits: Sequence[tuple[date, vf.Model]], days: Sequence[date], feats: Sequence[Array], log_iv: Array,
                 h: int) -> Array:
    """b = HAR-IV's log forecast of mean realized variance over the next ``h`` sessions minus log implied variance,
    from the newest stored fit on or before each session (NaN before the first fit or without coefficients)."""
    out = np.full(log_iv.shape, np.nan)
    ordinals = np.asarray([d.toordinal() for d in days])
    for k, (day, model) in enumerate(fits):
        c = model.coef.get("har_iv", {}).get(h)
        if c is None:
            continue
        lo = int(np.searchsorted(ordinals, day.toordinal()))
        hi = int(np.searchsorted(ordinals, fits[k + 1][0].toordinal())) if k + 1 < len(fits) else len(days)
        if hi <= lo:
            continue
        beta = c.beta
        x = beta[0] + beta[1] * feats[0][:, lo:hi] + beta[2] * feats[1][:, lo:hi] + beta[3] * feats[2][:, lo:hi]
        out[:, lo:hi] = x + beta[4] * log_iv[:, lo:hi] - log_iv[:, lo:hi]
    return out


def stored_fits(conn: Any) -> list[tuple[date, vf.Model]]:
    import json

    try:
        rows = conn.execute("SELECT fit_day, sha, model_json FROM stk_vol_fits WHERE version=? ORDER BY fit_day",
                            (vf.VERSION,)).fetchall()
    except sqlite3.OperationalError:            # a database from before the volatility forecast
        return []
    return [(date.fromordinal(int(r[0])), vf.Model.from_json(date.fromordinal(int(r[0])), r[1], json.loads(r[2])))
            for r in rows]


# ── data ─────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Data:
    rows: Array
    cols: Array
    x: dict[int, Array]              # horizon -> rows × NAMES
    y: dict[int, Array]
    b: dict[int, Array]


def _side(market: Any, symbols: Sequence[str], days: Sequence[date]) -> dict[str, Array]:
    """Term slope, skew and call−put spread from the option signals; days to the next results."""
    from desk.stocks.option_signals import MIN_LIQUID

    n, m = len(symbols), len(days)
    out = {k: np.full((n, m), np.nan) for k in ("term_slope", "skew", "iv_spread")}
    index = {s: i for i, s in enumerate(symbols)}
    col = {d.toordinal(): j for j, d in enumerate(days)}
    if n and m:
        closes = [cal.session_close(d) for d in days]
        try:
            for symbol, day, iv30, iv90, skew, spread, observed, computed in market.execute(
                    "SELECT symbol, day, iv30, iv90, skew, iv_spread, observed_at, computed_at "
                    "FROM stk_option_signals WHERE day>=? AND day<=? AND liquid>=? ORDER BY day",
                    (days[0].toordinal(), days[-1].toordinal(), MIN_LIQUID)):
                i, j = index.get(symbol), col.get(int(day))
                if i is None or j is None:
                    continue
                known = max(datetime.fromisoformat(observed), datetime.fromisoformat(computed))
                j = max(j, bisect_left(closes, known))
                if j >= m:
                    continue
                if iv30 and iv90:
                    out["term_slope"][i, j] = iv90 - iv30
                if skew is not None:
                    out["skew"][i, j] = skew
                if spread is not None:
                    out["iv_spread"][i, j] = spread
        except sqlite3.OperationalError:        # a database from before the option signals
            pass
    out = {k: pn._carry(v) for k, v in out.items()}
    out["earnings_days"] = pn.earnings_days(market, symbols, days)
    return out


def build(panel: vf.Panel, side: dict[str, Array], fits: Sequence[tuple[date, vf.Model]], *,
          rows: Array | None = None, cols: Array | None = None) -> Data:
    """Features, targets and the HAR baseline at the chosen cells (default: every ``STRIDE``-th session per stock
    with an implied volatility)."""
    v = np.where(np.isfinite(panel.rv), np.maximum(panel.rv, vf.VAR_FLOOR), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        feats = [np.log(v), np.log(vf.trailing_mean(v, vf.WEEK)), np.log(vf.trailing_mean(v, vf.MONTH))]
        log_iv = np.log(panel.iv)
    n_days = len(panel.days)
    if rows is None or cols is None:
        cand = np.argwhere(np.isfinite(log_iv) & np.isfinite(feats[2]))
        keep = (cand[:, 1] + cand[:, 0]) % STRIDE == 0
        rows, cols = cand[keep, 0].astype(np.int64), cand[keep, 1].astype(np.int64)
    ordinals = np.asarray([d.toordinal() for d in panel.days])
    out = Data(rows, cols, {}, {}, {})
    for h in HORIZONS:
        b = har_baseline(fits, panel.days, feats, log_iv, h)
        future = np.full(v.shape, np.nan)
        if n_days > h:
            future[:, :-h] = vf.trailing_mean(v, h)[:, h:]
        with np.errstate(invalid="ignore", divide="ignore"):
            y = np.log(future) - log_iv
        e_days = side["earnings_days"][rows, cols]
        ahead = cols + h < n_days             # a window still in the future (live): its calendar days, estimated
        window_days = np.where(ahead, ordinals[np.clip(cols + h, 0, n_days - 1)] - ordinals[cols],
                               h * 365.25 / 252).astype(float)
        inside = np.where(np.isfinite(e_days), (e_days <= window_days).astype(float), np.nan)
        x = np.column_stack([log_iv[rows, cols], log_iv[rows, cols] - feats[2][rows, cols], b[rows, cols],
                             feats[0][rows, cols], feats[1][rows, cols], feats[2][rows, cols],
                             side["term_slope"][rows, cols], side["skew"][rows, cols], side["iv_spread"][rows, cols],
                             e_days, inside]).astype(np.float32)
        out.x[h], out.y[h], out.b[h] = x, y[rows, cols].astype(np.float32), b[rows, cols].astype(np.float32)
    return out


# ── fit and evaluation ───────────────────────────────────────────────────────────────────────────
@dataclass
class HorizonFit:
    model: Any
    live: bool


@dataclass
class Fit:
    fit_day: date
    horizons: dict[int, HorizonFit] = field(default_factory=dict)


def _fit(x: Array, y: Array) -> Any:
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(**PARAMS).fit(x, y)


def evaluate(y: Array, pred: Array, base: Array, sessions: Array, stocks: Array, h: int) -> dict[str, Any]:
    n = len(y)
    if not n:
        return {"rows": 0, "sessions": 0, "stocks": 0, "live": False}
    em, eb = (y - pred) ** 2, (y - base) ** 2
    order = np.argsort(sessions, kind="stable")
    groups = np.split(order, np.flatnonzero(np.diff(sessions[order])) + 1)
    diff = np.asarray([float(eb[g].mean() - em[g].mean()) for g in groups])
    t = newey_west_t(diff, max(1, math.ceil(h / STRIDE)))
    mse_m, mse_b = float(em.mean()), float(eb.mean())
    n_sessions, n_stocks = len(groups), len(set(stocks.tolist()))
    enough = n >= GATE_ROWS and n_sessions >= GATE_SESSIONS and n_stocks >= GATE_STOCKS
    beats = mse_m < mse_b and t is not None and t >= GATE_T
    corr = float(np.corrcoef(pred - base, y - base)[0, 1]) if n > 2 and np.std(pred - base) > 0 else None
    return {"rows": n, "sessions": n_sessions, "stocks": n_stocks, "mse": round(mse_m, 6), "mse_har": round(mse_b, 6),
            "skill": round(1 - mse_m / mse_b, 5) if mse_b > 0 else None, "t": None if t is None else round(t, 3),
            "correction_corr": None if corr is None or not math.isfinite(corr) else round(corr, 4),
            "mean_surprise": round(float(np.mean(y)), 4), "enough": bool(enough), "beats": bool(beats),
            "live": bool(enough and beats)}


def walk_forward(data: Data, fit_col: int, fit_day: date, *, progress: Callable[[str], None] | None = None
                 ) -> tuple[Fit, dict[str, Any], int]:
    from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

    fit = Fit(fit_day)
    evaluation: dict[str, Any] = {}
    used = 0
    with threadpool_limits(THREADS):
        for h in HORIZONS:
            x, y, b = data.x[h], data.y[h], data.b[h]
            known = np.flatnonzero(np.isfinite(y) & np.isfinite(b) & (data.cols + h <= fit_col - 1))
            if len(known) < MIN_TRAIN:
                evaluation[str(h)] = {"rows": 0, "sessions": 0, "stocks": 0, "live": False,
                                      "note": f"only {len(known):,} rows with a known outcome and a HAR-IV fit"}
                continue
            ys, ps, bs, ss, ks = [], [], [], [], []
            end = fit_col - 1 - h
            for k in reversed(range(EVAL_BLOCKS)):
                hi = end - k * BLOCK
                lo = hi - BLOCK + 1
                c = data.cols[known]
                train, test = known[c + h <= lo - 1], known[(c >= lo) & (c <= hi)]
                if len(train) < MIN_TRAIN or len(test) < 50:
                    continue
                if progress:
                    progress(f"volatility surprise {h}-session horizon: held-out block {EVAL_BLOCKS - k}")
                tr = train if len(train) <= MAX_ROWS else np.sort(np.random.default_rng(fit_col + h + k).choice(
                    train, MAX_ROWS, replace=False))
                model = _fit(x[tr], y[tr])
                ys.append(y[test])
                ps.append(model.predict(x[test]))
                bs.append(b[test])
                ss.append(data.cols[test])
                ks.append(data.rows[test])
            ev = evaluate(np.concatenate(ys), np.concatenate(ps), np.concatenate(bs), np.concatenate(ss),
                          np.concatenate(ks), h) if ys else {"rows": 0, "sessions": 0, "stocks": 0, "live": False,
                                                             "note": "no held-out block yet"}
            evaluation[str(h)] = ev
            final = known if len(known) <= MAX_ROWS else np.sort(np.random.default_rng(fit_col * 3 + h).choice(
                known, MAX_ROWS, replace=False))
            used += len(final)
            fit.horizons[h] = HorizonFit(_fit(x[final], y[final]), bool(ev.get("live")))
    return fit, evaluation, used


def refit(market: Any, fit_day: date, now: datetime, *, progress: Callable[[str], None] | None = None,
          symbols: Sequence[str] | None = None) -> dict[str, Any]:
    """Fit on ``fit_day`` (once) from the stored realized and implied variance and the stored HAR-IV fits."""
    newest = store.newest_day(market, MODEL, VERSION)
    if newest is not None and newest >= fit_day:
        return {"fitted": False, "reason": f"a fit for {fit_day.isoformat()} or later exists"}
    started = time.monotonic()
    chosen = list(symbols) if symbols is not None else vf.tracked(market, fit_day)
    if progress:
        progress(f"volatility surprise: loading {len(chosen)} stocks' realized and implied variance")
    panel = vf.load_panel(market, chosen)
    fits = stored_fits(market)
    if not panel.days or not fits:
        return {"fitted": False, "reason": "no realized variance or no HAR-IV fit stored yet"}
    ordinals = [d.toordinal() for d in panel.days]
    fit_col = int(np.searchsorted(np.asarray(ordinals), fit_day.toordinal(), side="right")) - 1
    if fit_col < 0:
        return {"fitted": False, "reason": "no stored session on or before the fit day"}
    data = build(panel, _side(market, panel.symbols, panel.days), fits)
    fit, evaluation, used = walk_forward(data, fit_col, fit_day, progress=progress)
    if not fit.horizons:
        return {"fitted": False, "reason": "not enough rows with a known outcome and a HAR-IV fit"}
    seconds = time.monotonic() - started
    live = {h: hf.live for h, hf in fit.horizons.items()}
    stocks = len(set(data.rows.tolist()))
    store.save(market, MODEL, VERSION, fit_day, fit, live, evaluation, used, stocks, seconds, peak_mb(), now)
    return {"fitted": True, "fit_day": fit_day.isoformat(), "rows": used, "stocks": stocks,
            "live": {str(h): v for h, v in live.items()}, "seconds": round(seconds, 1), "peak_mb": round(peak_mb(), 1)}


# ── live predictions ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Correction:
    """The predicted log variance correction to the HAR-IV forecast at one horizon, and whether it is used."""
    log_var: float
    live: bool

    @property
    def multiplier(self) -> float:
        """The volatility multiplier applied (1 when not live)."""
        if not self.live:
            return 1.0
        return float(min(BOUNDS[1], max(BOUNDS[0], math.exp(self.log_var / 2))))


@dataclass
class Session:
    session: date
    fit: Fit | None
    record: store.Stored | None
    by_symbol: dict[str, dict[int, float]] = field(default_factory=dict)
    reason: str | None = None

    def at(self, symbol: str, sessions: int) -> Correction | None:
        got = self.by_symbol.get(symbol)
        if not got or self.fit is None:
            return None
        h = max(1, int(sessions))
        top = HORIZONS[-1]
        if h >= top:
            if top not in got:
                return None
            return Correction(got[top] * top / h, self.fit.horizons[top].live)
        lo = max(x for x in HORIZONS if x <= h)
        hi = min(x for x in HORIZONS if x >= h)
        if lo not in got or hi not in got:
            return None
        mix = 0.0 if hi == lo else (h - lo) / (hi - lo)
        return Correction((1 - mix) * got[lo] + mix * got[hi],
                          self.fit.horizons[lo].live and self.fit.horizons[hi].live)


def for_session(market: Any, symbols: Sequence[str], session: date, loader: store.Loader | None = None) -> Session:
    """The correction for ``symbols`` on ``session`` (inputs as of its close), from the newest fit on or before it."""
    loader = loader or store.Loader(market, MODEL, VERSION)
    got = loader.on(session)
    if got is None:
        return Session(session, None, None, reason="no volatility-surprise fit on or before this session")
    record, fit = got
    out = Session(session, fit, record)
    wanted = list(symbols)
    if not wanted:
        return out
    days = cal.trading_days(session - timedelta(days=60), session)[-vf.MONTH - 5:]
    panel = _recent_panel(market, wanted, days)
    model = vf.model_on(market, session)
    if model is None:
        out.reason = "no HAR-IV fit on or before this session"
        return out
    col = len(days) - 1
    rows = np.arange(len(wanted))
    data = build(panel, _side(market, wanted, days), [(model.fit_day, model)], rows=rows,
                 cols=np.full(len(rows), col))
    for h, hf in fit.horizons.items():
        x = data.x[h]
        ok = np.isfinite(data.b[h])
        if not ok.any():
            continue
        pred = hf.model.predict(x[ok]) - data.b[h][ok]
        for i, c in zip(np.flatnonzero(ok), pred, strict=True):
            out.by_symbol.setdefault(wanted[i], {})[h] = float(c)
    return out


def _recent_panel(market: Any, symbols: Sequence[str], days: Sequence[date]) -> vf.Panel:
    """``vol_forecast``'s panel restricted to recent sessions (realized variance and implied variance)."""
    shape = (len(symbols), len(days))
    rv, ret, iv = np.full(shape, np.nan), np.full(shape, np.nan), np.full(shape, np.nan)
    index = {s: i for i, s in enumerate(symbols)}
    col = {d.toordinal(): j for j, d in enumerate(days)}
    first, last = days[0].toordinal(), days[-1].toordinal()
    def rows(sql: str) -> list[Any]:
        try:
            return list(market.execute(sql, (first, last)))
        except sqlite3.OperationalError:          # a database from before that table
            return []

    for symbol, day, v, r in rows("SELECT symbol, day, rv, ret FROM stk_rv_daily WHERE day>=? AND day<=?"):
        if symbol in index and (j := col.get(int(day))) is not None:
            rv[index[symbol], j], ret[index[symbol], j] = v, r
    for symbol, day, value in rows("SELECT symbol, day, iv30 FROM stk_iv_daily WHERE day>=? AND day<=?"):
        if symbol in index and (j := col.get(int(day))) is not None and value and value > 0:
            iv[index[symbol], j] = value * value / vf.TRADING_YEAR
    for symbol, day, value in rows("SELECT underlying, day, json_extract(summary_json, '$.iv30') FROM stk_option_daily "
                                   "WHERE day>=? AND day<=?"):
        if symbol in index and (j := col.get(int(day))) is not None and value and float(value) > 0:
            iv[index[symbol], j] = float(value) ** 2 / vf.TRADING_YEAR
    return vf.Panel(list(symbols), list(days), rv, ret, iv)


__all__ = ["BOUNDS", "HORIZONS", "MODEL", "NAME", "NAMES", "VERSION", "Correction", "Data", "Fit", "HorizonFit",
           "Session", "build", "content", "evaluate", "for_session", "har_baseline", "refit", "stored_fits",
           "version_hash", "walk_forward"]
