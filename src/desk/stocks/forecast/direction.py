"""The direction model: a distribution of each stock's log return over 1, 5, 10, 21 and 63 sessions, learned from
the saved daily bars of the whole universe (docs/STOCKS_METHOD.md → Direction and volatility).

**What it predicts.** For a decision on session S about a stock, quantiles (``QUANTILES``) of its log return from S's
close to h sessions later, **standardized** by the stock's reference scale (root mean square daily return over the
63 sessions to S − 1, × √h; ``panel.REF_SESSIONS``). Standardizing puts a sleepy utility and a biotech on one scale,
so the trees learn the shape and the tilt of the distribution, not each stock's volatility (that is the volatility
model's job).

**How.** Gradient-boosted quantile trees (scikit-learn's ``HistGradientBoostingRegressor``, ``loss='quantile'``), one
per horizon and quantile, on the features of ``panel.NAMES`` (all as of S − 1; missing stays missing). Option-based
characteristics, momentum and reversal are among them because the option-return literature finds them the strongest
predictors (Bali, Beckmeyer, Moerke & Weigert 2021). The predicted quantiles of one row are sorted (rearrangement,
Chernozhukov, Fernández-Val & Galichon 2010), so they never cross.

**Data, bounded.** The ``MAX_STOCKS`` stocks with the most dollar volume over their whole stored history (at least
300 sessions), every ``STRIDE``-th session per stock (offset per stock, so every session has rows), rows only where the
stock traded at least ``MIN_DOLLAR_VOLUME`` a day over the previous 20 sessions (known then). At most ``MAX_ROWS``
rows per final fit and ``EVAL_ROWS`` per evaluation fit (seeded uniform samples), ``THREADS`` threads.

**Walk-forward, purged, weekly.** A fit on day F (the first session of its week) trains on rows whose outcome was
known before F (S + h ≤ F − 1). Its evaluation: the ``EVAL_BLOCKS`` blocks of ``BLOCK`` sessions before F, each
predicted by models trained only on rows whose outcome was known before the block began (the purge), against the
**baseline**: the same training rows' unconditional quantiles of the standardized return (no skill; today's
drift-neutral behaviour). Scored by pinball loss (the proper loss for quantiles) and calibration (how often the realized
return fell below each predicted quantile), and market-neutrally by the cross-sectional rank correlation of the
predicted median with the realized return on each session (a correlation within one session ignores the market's move).

**Gate, per horizon.** Live only with at least ``GATE_ROWS`` held-out rows over ``GATE_SESSIONS`` sessions and
``GATE_STOCKS`` stocks, a lower pinball loss than the baseline with a Newey–West t-statistic of at least ``GATE_T``
across sessions, and a calibration error no worse than the baseline's + ``CAL_SLACK``. Otherwise that horizon uses the
baseline: KESTREL behaves as before (drift-neutral).
"""

from __future__ import annotations

import math
import resource
import time
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np

from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks.forecast import panel as pn
from desk.stocks.forecast import store

Array = np.ndarray
NAME = "stocks_direction_model"
VERSION = 1
MODEL = "direction"
HORIZONS = (1, 5, 10, 21, 63)
QUANTILES = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
MAX_STOCKS = 1200
STRIDE = 5
MIN_HISTORY = 253                # sessions before a row (12-1 momentum and the 52-week high need a year)
MIN_DOLLAR_VOLUME = 2e6
MAX_ROWS = 150_000
EVAL_ROWS = 80_000
EVAL_BLOCKS = 3
BLOCK = 252
MIN_TRAIN = 5_000
GATE_ROWS, GATE_SESSIONS, GATE_STOCKS = 5_000, 60, 30
GATE_T = 2.0
CAL_SLACK = 0.01
THREADS = 1                      # one thread: a laptop shares its cores (OpenMP threads contend badly)
PARAMS = {"loss": "quantile", "max_iter": 80, "learning_rate": 0.08, "max_leaf_nodes": 15, "min_samples_leaf": 200,
          "l2_regularization": 1.0, "early_stopping": False, "random_state": 11}


def content() -> dict[str, Any]:
    return {"version": VERSION, "horizons": list(HORIZONS), "quantiles": list(QUANTILES), "features": list(pn.NAMES),
            "target": "log return from the decision close over h sessions / (reference scale as of the previous "
                      "close x sqrt(h)); reference = RMS daily log return over 63 sessions, floored",
            "ref_sessions": pn.REF_SESSIONS, "var_floor": pn.VAR_FLOOR, "fresh": pn.FRESH,
            "max_stocks": MAX_STOCKS, "stride": STRIDE, "min_history": MIN_HISTORY,
            "min_dollar_volume": MIN_DOLLAR_VOLUME, "max_rows": MAX_ROWS, "eval_rows": EVAL_ROWS,
            "eval_blocks": EVAL_BLOCKS, "block": BLOCK, "min_train": MIN_TRAIN, "params": PARAMS,
            "rearrangement": "sorted predicted quantiles", "baseline": "unconditional quantiles of the training rows",
            "gate": {"rows": GATE_ROWS, "sessions": GATE_SESSIONS, "stocks": GATE_STOCKS, "t": GATE_T,
                     "cal_slack": CAL_SLACK, "loss": "pinball, mean over quantiles, Newey-West t across sessions"},
            "fit": "weekly on the first session of the week, rows with S + h <= F - 1"}


def version_hash() -> str:
    return sha256_hex(canonical_json(content()))[:16]


# ── the fitted model ─────────────────────────────────────────────────────────────────────────────
@dataclass
class HorizonFit:
    models: list[Any]                # one quantile regressor per QUANTILES
    baseline: list[float]            # unconditional quantiles of the training rows
    live: bool


@dataclass
class Fit:
    fit_day: date
    features: tuple[str, ...]
    horizons: dict[int, HorizonFit] = field(default_factory=dict)

    def predict(self, x: Array, h: int) -> Array:
        """Sorted predicted quantiles (rows × QUANTILES) at fitted horizon ``h``."""
        hf = self.horizons[h]
        out = np.column_stack([m.predict(x) for m in hf.models])
        return np.sort(out, axis=1)


def pinball(z: Array, q: Array) -> Array:
    """Pinball loss of each row (rows × quantiles): τ·(z − q) above, (τ − 1)·(z − q) below."""
    tau = np.asarray(QUANTILES)[None, :]
    d = z[:, None] - q
    out: Array = np.maximum(tau * d, (tau - 1) * d)
    return out


def newey_west_t(x: Array, lags: int) -> float | None:
    """t-statistic of the mean of a series with Newey–West (Bartlett) standard errors."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 3:
        return None
    e = x - x.mean()
    var = float(e @ e) / n
    for k in range(1, min(lags, n - 1) + 1):
        var += 2 * (1 - k / (lags + 1)) * float(e[k:] @ e[:-k]) / n
    if var <= 0:
        return None
    return float(x.mean() / math.sqrt(var / n))


def _spearman(a: Array, b: Array) -> float | None:
    from scipy.stats import rankdata

    if len(a) < 5 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return None
    ra, rb = rankdata(a), rankdata(b)
    return float(np.corrcoef(ra, rb)[0, 1])


def evaluate(z: Array, pred: Array, base: Array, sessions: Array, stocks: Array, h: int) -> dict[str, Any]:
    """Held-out scores of one horizon: pinball and calibration against the baseline, the market-neutral rank
    correlation of the predicted median, and the gate."""
    n = len(z)
    if not n:
        return {"rows": 0, "sessions": 0, "stocks": 0, "live": False}
    lm, lb = pinball(z, pred), pinball(z, base)
    model_loss, base_loss = float(lm.mean()), float(lb.mean())
    order = np.argsort(sessions, kind="stable")
    s_sorted = sessions[order]
    cuts = np.flatnonzero(np.diff(s_sorted)) + 1
    groups = np.split(order, cuts)
    diff = np.asarray([float(lb[g].mean() - lm[g].mean()) for g in groups])
    lags = max(1, math.ceil(h / STRIDE))
    t = newey_west_t(diff, lags)
    cov_m = (z[:, None] <= pred).mean(axis=0)
    cov_b = (z[:, None] <= base).mean(axis=0)
    tau = np.asarray(QUANTILES)
    cal_m, cal_b = float(np.abs(cov_m - tau).mean()), float(np.abs(cov_b - tau).mean())
    mid = QUANTILES.index(0.5)
    ics = [ic for g in groups if (ic := _spearman(pred[g, mid], z[g])) is not None]
    ic_t = newey_west_t(np.asarray(ics), lags) if ics else None
    n_sessions, n_stocks = len(groups), len(set(stocks.tolist()))
    enough = n >= GATE_ROWS and n_sessions >= GATE_SESSIONS and n_stocks >= GATE_STOCKS
    beats = model_loss < base_loss and t is not None and t >= GATE_T and cal_m <= cal_b + CAL_SLACK
    return {"rows": n, "sessions": n_sessions, "stocks": n_stocks, "pinball": round(model_loss, 6),
            "pinball_baseline": round(base_loss, 6),
            "improvement": round(1 - model_loss / base_loss, 5) if base_loss > 0 else None,
            "t": None if t is None else round(t, 3),
            "coverage": [round(float(c), 4) for c in cov_m], "coverage_baseline": [round(float(c), 4) for c in cov_b],
            "calibration_error": round(cal_m, 5), "calibration_error_baseline": round(cal_b, 5),
            "neutral_ic": round(float(np.mean(ics)), 5) if ics else None,
            "neutral_ic_t": None if ic_t is None else round(ic_t, 3),
            "enough": bool(enough), "beats": bool(beats), "live": bool(enough and beats)}


# ── data ─────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Data:
    """The sampled rows of a panel: stock, session column, features, standardized return per horizon."""
    rows: Array
    cols: Array
    x: Array
    z: dict[int, Array]


def _offset(symbol: str) -> int:
    return zlib.crc32(symbol.encode()) % STRIDE


def sample(p: pn.Panel, *, last: int | None = None, stride: int = STRIDE) -> tuple[Array, Array]:
    """Every ``stride``-th session per stock (offset by stock) from ``MIN_HISTORY`` on, through column ``last``."""
    n_days = len(p.days) if last is None else last + 1
    rows, cols = [], []
    for i, s in enumerate(p.symbols):
        c = np.arange(MIN_HISTORY + (_offset(s) - MIN_HISTORY) % stride, n_days, stride)
        rows.append(np.full(len(c), i))
        cols.append(c)
    if not rows:
        return np.zeros(0, int), np.zeros(0, int)
    return np.concatenate(rows).astype(np.int64), np.concatenate(cols).astype(np.int64)


def dataset(p: pn.Panel) -> Data:
    rows, cols = sample(p)
    feats = pn.features(p, rows, cols)
    liquid = np.nan_to_num(feats["log_dollar_volume"], nan=-1.0) >= math.log10(MIN_DOLLAR_VOLUME)
    keep = liquid & np.isfinite(feats["log_sigma_ref"]) & np.isfinite(feats["ret_1m"])
    rows, cols = rows[keep], cols[keep]
    x = pn.matrix(feats)[keep]
    z = {h: pn.standardized_returns(p, rows, cols, h)[0].astype(np.float32) for h in HORIZONS}
    return Data(rows, cols, x, z)


def _subsample(idx: Array, n: int, seed: int) -> Array:
    if len(idx) <= n:
        return idx
    return np.sort(np.random.default_rng(seed).choice(idx, n, replace=False))


def _fit_quantiles(x: Array, z: Array) -> list[Any]:
    from sklearn.ensemble import HistGradientBoostingRegressor

    return [HistGradientBoostingRegressor(**PARAMS, quantile=tau).fit(x, z) for tau in QUANTILES]


def walk_forward(data: Data, fit_col: int, fit_day: date, *, progress: Callable[[str], None] | None = None
                 ) -> tuple[Fit, dict[str, Any], int]:
    """The fit on column ``fit_col`` and its purged out-of-sample evaluation. Returns (fit, evaluation, rows)."""
    from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

    fit = Fit(fit_day, tuple(pn.NAMES))
    evaluation: dict[str, Any] = {}
    used = 0
    with threadpool_limits(THREADS):
        for h in HORIZONS:
            z = data.z[h]
            known = np.flatnonzero(np.isfinite(z) & (data.cols + h <= fit_col - 1))
            if len(known) < MIN_TRAIN:
                evaluation[str(h)] = {"rows": 0, "sessions": 0, "stocks": 0, "live": False,
                                      "note": f"only {len(known):,} rows with a known outcome"}
                continue
            zs, ps, bs, ss, ks = [], [], [], [], []
            end = fit_col - 1 - h
            for k in reversed(range(EVAL_BLOCKS)):
                hi = end - k * BLOCK
                lo = hi - BLOCK + 1
                c = data.cols[known]
                train = known[c + h <= lo - 1]
                test = known[(c >= lo) & (c <= hi)]
                if len(train) < MIN_TRAIN or len(test) < 100:
                    continue
                if progress:
                    progress(f"direction {h}-session horizon: held-out block {EVAL_BLOCKS - k} of {EVAL_BLOCKS}")
                tr = _subsample(train, EVAL_ROWS, fit_col * 31 + h * 7 + k)
                models = _fit_quantiles(data.x[tr], data.z[h][tr])
                pred = np.sort(np.column_stack([m.predict(data.x[test]) for m in models]), axis=1)
                base = np.quantile(z[tr], QUANTILES)
                zs.append(z[test])
                ps.append(pred)
                bs.append(np.broadcast_to(base, pred.shape))
                ss.append(data.cols[test])
                ks.append(data.rows[test])
            if zs:
                ev = evaluate(np.concatenate(zs), np.concatenate(ps), np.concatenate(bs), np.concatenate(ss),
                              np.concatenate(ks), h)
            else:
                ev = {"rows": 0, "sessions": 0, "stocks": 0, "live": False, "note": "no held-out block yet"}
            evaluation[str(h)] = ev
            if progress:
                progress(f"direction {h}-session horizon: final fit")
            final = _subsample(known, MAX_ROWS, fit_col * 17 + h)
            used += len(final)
            fit.horizons[h] = HorizonFit(_fit_quantiles(data.x[final], z[final]),
                                         [float(v) for v in np.quantile(z[final], QUANTILES)], bool(ev.get("live")))
    return fit, evaluation, used


# ── the weekly job ───────────────────────────────────────────────────────────────────────────────
def peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def refit(market: Any, research: Any, fit_day: date, now: datetime, *,
          progress: Callable[[str], None] | None = None, symbols: Sequence[str] | None = None) -> dict[str, Any]:
    """Fit on ``fit_day`` (once; a stored fit day is never refitted) from every saved daily bar up to it."""
    newest = store.newest_day(market, MODEL, VERSION)
    if newest is not None and newest >= fit_day:
        return {"fitted": False, "reason": f"a fit for {fit_day.isoformat()} or later exists"}
    started = time.monotonic()
    chosen = list(symbols) if symbols is not None else pn.candidates(market, MAX_STOCKS)
    if progress:
        progress(f"direction: loading {len(chosen)} stocks' daily bars")
    p = pn.load(market, chosen, fit_day, research=research)
    fit_col = p.col(fit_day)
    if fit_col is None or not chosen:
        return {"fitted": False, "reason": "no stored bars through the fit day"}
    data = dataset(p)
    del p
    fit, evaluation, used = walk_forward(data, fit_col, fit_day, progress=progress)
    if not fit.horizons:
        return {"fitted": False, "reason": f"not enough rows with known outcomes ({len(data.rows):,} sampled)",
                "rows": len(data.rows)}
    seconds = time.monotonic() - started
    live = {h: hf.live for h, hf in fit.horizons.items()}
    stocks = len(set(data.rows.tolist()))
    store.save(market, MODEL, VERSION, fit_day, fit, live, evaluation, used, stocks, seconds, peak_mb(), now)
    return {"fitted": True, "fit_day": fit_day.isoformat(), "rows": used, "sampled": len(data.rows),
            "stocks": stocks, "live": {str(h): v for h, v in live.items()}, "seconds": round(seconds, 1),
            "peak_mb": round(peak_mb(), 1)}


# ── live predictions ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Quantiles:
    """One stock's standardized-return quantiles at one horizon (``QUANTILES``): the model's (``cond``) and the
    baseline's, whether the model is live there, and the reference daily scale (to turn them into returns)."""
    cond: tuple[float, ...]
    base: tuple[float, ...]
    live: bool
    sigma_ref: float
    sessions: int

    def returns(self) -> tuple[float, ...]:
        """The model's quantiles as log returns over the horizon."""
        scale = self.sigma_ref * math.sqrt(self.sessions)
        return tuple(q * scale for q in self.cond)


@dataclass
class Session:
    """The direction model's predictions for the stocks of one session (``for_session``)."""
    session: date
    fit: Fit | None
    record: store.Stored | None
    by_symbol: dict[str, dict[int, tuple[Array, bool]]] = field(default_factory=dict)
    sigma_ref: dict[str, float] = field(default_factory=dict)
    reason: str | None = None

    def at(self, symbol: str, sessions: int) -> Quantiles | None:
        """Quantiles at ``sessions`` (linear in the horizon between the fitted ones; clamped to 1–63), or None when
        the stock had no features or there is no fit. Live only when both neighbouring horizons are."""
        got = self.by_symbol.get(symbol)
        ref = self.sigma_ref.get(symbol)
        if not got or ref is None or self.fit is None:
            return None
        h = max(HORIZONS[0], min(HORIZONS[-1], int(sessions)))
        lo = max(x for x in HORIZONS if x <= h)
        hi = min(x for x in HORIZONS if x >= h)
        if lo not in got or hi not in got:
            return None
        mix = 0.0 if hi == lo else (h - lo) / (hi - lo)
        cond = (1 - mix) * got[lo][0] + mix * got[hi][0]
        base = (1 - mix) * np.asarray(self.fit.horizons[lo].baseline) + mix * np.asarray(self.fit.horizons[hi].baseline)
        return Quantiles(tuple(float(v) for v in np.sort(cond)), tuple(float(v) for v in base),
                         bool(got[lo][1] and got[hi][1]), ref, max(1, int(sessions)))


def for_session(market: Any, research: Any, symbols: Sequence[str], session: date,
                loader: store.Loader | None = None) -> Session:
    """Predict every horizon for ``symbols`` decided on ``session``, from the newest fit on or before it (features as
    of the previous close)."""
    loader = loader or store.Loader(market, MODEL, VERSION)
    got = loader.on(session)
    if got is None:
        return Session(session, None, None, reason="no direction fit on or before this session")
    record, fit = got
    p = pn.load(market, list(symbols), session, sessions=MIN_HISTORY + 30, research=research)
    out = Session(session, fit, record)
    col = p.col(session)
    if col is None or not p.symbols:
        out.reason = "the session is not a stored trading day"
        return out
    rows = np.arange(len(p.symbols))
    cols = np.full(len(rows), col)
    feats = pn.features(p, rows, cols)
    x = pn.matrix(feats)
    ref = np.exp(feats["log_sigma_ref"].astype(float))
    ok = np.isfinite(ref)
    preds = {h: fit.predict(x, h) for h in fit.horizons}
    for i, s in enumerate(p.symbols):
        if not ok[i]:
            continue
        out.sigma_ref[s] = float(ref[i])
        out.by_symbol[s] = {h: (preds[h][i], fit.horizons[h].live) for h in fit.horizons}
    return out


__all__ = ["HORIZONS", "MODEL", "NAME", "QUANTILES", "VERSION", "Data", "Fit", "HorizonFit", "Quantiles", "Session",
           "content", "dataset", "evaluate", "for_session", "newey_west_t", "pinball", "refit", "sample",
           "version_hash", "walk_forward"]
