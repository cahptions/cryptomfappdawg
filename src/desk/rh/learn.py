"""The Robinhood desk's learning lab: it keeps training as data arrives and is judged only on data it never saw.

How it learns (docs/ROBINHOOD_LEARNING.md):

* A fixed family of ~220 rule variants (trend breakouts, weekly relative momentum, uptrend pullbacks, each over a
  grid of settings) plus an L2 logistic model are simulated over years of closed daily Coinbase bars, every trade
  paying the coin's measured Robinhood round trip (or the configured assumption until measured).
* Walk-forward, month by month: at each fold start the lab chooses using only trades whose outcome was already
  known in the previous 365 days, then trades the next 30 days it has not seen. The record that counts is the
  concatenation of those unseen months. Picking "the best of 220 on the same data" would only find luck; judging
  the picking procedure on unseen months does not.
* Three procedures compete: the single best variant, the best three, and the model. Each must, on unseen months,
  be profitable after costs and beat holding BTC over the very same days (stationary bootstrap, Holm-adjusted
  across the three), with enough months and trades. The best passing procedure is promoted to PAPER as
  ``L1_LEARNED`` and refit monthly; a forward check benches it if its paper results contradict the history.

Nothing here can place a real order; promotion only changes what the PAPER book does.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from numpy.typing import NDArray

from desk.core.hashing import canonical_json, sha256_hex
from desk.models.logistic import fit as fit_logistic
from desk.rh.contracts import STABLECOINS, Candle
from desk.stats.core import holm, stationary_bootstrap_means

Array = NDArray[np.float64]
Mask = NDArray[np.bool_]

LAB_VERSION = 2   # v1 (2026-10-20): TREND/XMOM/REVERT + model; none passed on unseen months at a 2% round trip.
                  # v2 adds low-turnover families (TIMING, monthly XMOM) because costs dominated; still judged unseen.
TRAIN_DAYS = 365                 # a choice looks at the last year only, so it adapts to the market's regime
TEST_DAYS = 30                   # then trades the next 30 days unseen
MODEL_TRAIN_DAYS = 730
WARMUP_DAYS = 210                # the longest feature (200-day average) plus a margin
MIN_TRAIN_TRADES = 3              # v2: per-trade scoring with >= 15 trades could never pick a slow TIMING hold
MIN_FOLDS = 12
MIN_OOS_TRADES = 60
ALPHA_PROFIT = 0.05              # one-sided, Holm-adjusted across the procedures
ALPHA_BTC = 0.10
MAX_POSITIONS = 4                # the learned strategy's share of the PAPER book
MIN_DOLLAR_VOLUME = 1_000_000.0
STOP_ATR, STOP_MIN, STOP_MAX = 2.0, 0.05, 0.25
MODEL_HOLD = 7
MODEL_TOP = 3
MODEL_THRESHOLD = 0.55
RESAMPLES = 2000
HISTORY_DAYS = 1460              # four years of daily bars are downloaded for the lab
PROCEDURES = ("LEARN_BEST", "LEARN_TOP3", "LEARN_MODEL")
STRATEGY_ID = "L1_LEARNED"
BTC = "BTC-USD"
MODEL_FEATURES = ("ret1", "ret3", "ret7", "ret14", "ret28", "ret56", "dist_hh20", "dist_hh55", "dist_sma50",
                  "dist_sma200", "vol20", "rsi2", "volume_trend", "btc_ret28", "btc_on")


# ── candidates ──────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Candidate:
    family: str                                   # TREND | XMOM | REVERT | TIMING
    params: tuple[tuple[str, int | float | bool | str], ...]

    @property
    def id(self) -> str:
        return self.family + ":" + ",".join(f"{k}={v}" for k, v in self.params)

    def p(self, key: str, default: Any = None) -> Any:
        return dict(self.params).get(key, default)

    def describe(self) -> str:
        p = dict(self.params)
        regime = " while BTC is above its 100-day average" if p.get("regime") else ""
        if self.family == "TREND":
            trend = f", above its {p['sma']}-day average" if p["sma"] else ""
            return (f"Buy a close above the prior {p['n']}-day high{trend}{regime}; sell at a {p['x']}-day low, "
                    f"a {p['k']}×ATR trailing stop or after {p['hold']} days.")
        if self.family == "XMOM":
            when = "Every Monday" if p.get("every", 1) == 1 else f"Every {p['every']} weeks"
            return (f"{when} hold the {p['top']} coins with the strongest {p['look']}-day run that beat "
                    f"BTC{regime}.")
        if self.family == "TIMING":
            coins = {"BTC": "BTC", "MAJORS2": "BTC and ETH", "TOP5": "the 5 most-traded coins"}[str(p["coins"])]
            return f"Hold {coins} while above the {p['sma']}-day average; sell on a close below it."
        return (f"Buy a 2-day RSI dip below {p['rsi']} in a coin above its {p['sma']}-day average{regime}; "
                f"sell on the bounce above the 5-day average or after {p['hold']} days.")


def candidates() -> list[Candidate]:
    out = []
    for n in (10, 20, 55):
        for sma in (0, 50, 100):
            for x in (5, 10, 20):
                for k in (2.0, 3.0, 4.0):
                    for regime in (True, False):
                        out.append(Candidate("TREND", (("n", n), ("sma", sma), ("x", x), ("k", k), ("hold", 60),
                                                       ("regime", regime))))
    for look in (7, 14, 28, 56, 91):
        for top in (1, 3, 5):
            for regime in (True, False):
                for every in (1, 4):
                    out.append(Candidate("XMOM", (("look", look), ("top", top), ("regime", regime),
                                                  ("every", every))))
    for sma in (50, 100, 200):
        for coins in ("BTC", "MAJORS2", "TOP5"):
            out.append(Candidate("TIMING", (("sma", sma), ("coins", coins), ("hold", 365))))
    for rsi in (5, 10, 20):
        for sma in (100, 200):
            for hold in (3, 7):
                for regime in (True, False):
                    out.append(Candidate("REVERT", (("rsi", rsi), ("sma", sma), ("hold", hold), ("regime", regime))))
    return out


def lab_spec() -> dict[str, Any]:
    """Everything that defines how the lab learns; its hash versions every learned selection."""
    return {"lab_version": LAB_VERSION, "train_days": TRAIN_DAYS, "test_days": TEST_DAYS,
            "model_train_days": MODEL_TRAIN_DAYS, "warmup_days": WARMUP_DAYS, "min_train_trades": MIN_TRAIN_TRADES,
            "selection_score": "sum(net) - sqrt(n)*sd(net) over the last train_days, n >= min_train_trades",
            "min_folds": MIN_FOLDS, "min_oos_trades": MIN_OOS_TRADES, "alpha_profit": ALPHA_PROFIT,
            "alpha_btc": ALPHA_BTC, "max_positions": MAX_POSITIONS, "min_dollar_volume": MIN_DOLLAR_VOLUME,
            "stop": [STOP_ATR, STOP_MIN, STOP_MAX], "model": {"features": MODEL_FEATURES, "hold": MODEL_HOLD,
                                                             "top": MODEL_TOP, "threshold": MODEL_THRESHOLD, "l2": 1.0},
            "candidates": [c.id for c in candidates()], "procedures": PROCEDURES}


def lab_sha256() -> str:
    return sha256_hex(canonical_json(lab_spec()))


# ── data ────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Panel:
    """Daily bars for every coin on one calendar; NaN where a coin had no bar. Index t = the bar of day t."""

    days: list[date]
    symbols: list[str]
    o: Array
    h: Array
    low: Array
    c: Array
    v: Array
    f: dict[str, Array] = field(default_factory=dict)

    def index(self, day: date) -> int:
        return self.days.index(day)


def build_panel(history: dict[str, list[Candle]], *, until: date | None = None) -> Panel:
    """``history`` maps a Robinhood symbol to closed daily bars; bars after ``until`` are ignored."""
    symbols = sorted(s for s, bars in history.items() if bars and s.split("-")[0] not in STABLECOINS)
    if BTC in symbols:
        symbols.remove(BTC)
        symbols.insert(0, BTC)
    all_days = sorted({b.start.date() for s in symbols for b in history[s] if until is None or b.start.date() <= until})
    if not all_days:
        raise ValueError("no history")
    first, last = all_days[0], all_days[-1]
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    pos = {d: i for i, d in enumerate(days)}
    shape = (len(symbols), len(days))
    arrays = {k: np.full(shape, np.nan) for k in ("o", "h", "low", "c", "v")}
    for si, s in enumerate(symbols):
        for b in history[s]:
            d = b.start.date()
            if d in pos:
                t = pos[d]
                arrays["o"][si, t], arrays["h"][si, t], arrays["low"][si, t] = float(b.open), float(b.high), float(b.low)
                arrays["c"][si, t], arrays["v"][si, t] = float(b.close), float(b.volume)
    panel = Panel(days, symbols, arrays["o"], arrays["h"], arrays["low"], arrays["c"], arrays["v"])
    panel.f = features(panel)
    return panel


def _rolling(x: Array, n: int, how: str, *, shift: int = 0) -> Array:
    """Rolling statistic over n bars ending at t - shift (shift=1: the n bars before t). NaN inside -> NaN."""
    out = np.full(x.shape, np.nan)
    if x.shape[1] < n + shift:
        return out
    view = sliding_window_view(x, n, axis=1)            # view[:, i] covers bars i..i+n-1
    stat = {"max": view.max(axis=2), "min": view.min(axis=2), "mean": view.mean(axis=2),
            "std": view.std(axis=2), "median": np.median(view, axis=2)}[how]
    out[:, n - 1 + shift:] = stat[:, :x.shape[1] - n + 1 - shift]
    return out


def _rsi(c: Array, n: int) -> Array:
    """Wilder's RSI along each row; NaN until n changes are available or after a gap."""
    out = np.full(c.shape, np.nan)
    for s in range(c.shape[0]):
        gain = loss = math.nan
        count = 0
        for t in range(1, c.shape[1]):
            change = c[s, t] - c[s, t - 1]
            if math.isnan(change):
                gain = loss = math.nan
                count = 0
                continue
            up, down = max(change, 0.0), max(-change, 0.0)
            count += 1
            if count <= n:
                gain = (0.0 if count == 1 else gain) + up / n
                loss = (0.0 if count == 1 else loss) + down / n
                if count < n:
                    continue
            else:
                gain = (gain * (n - 1) + up) / n
                loss = (loss * (n - 1) + down) / n
            out[s, t] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)
    return out


def features(p: Panel) -> dict[str, Array]:
    c, h, low = p.c, p.h, p.low
    with np.errstate(invalid="ignore", divide="ignore"):
        prev = np.concatenate([np.full((c.shape[0], 1), np.nan), c[:, :-1]], axis=1)
        tr = np.fmax(h - low, np.fmax(np.abs(h - prev), np.abs(low - prev)))
        tr[np.isnan(prev) | np.isnan(h)] = np.nan
        log_ret = np.log(c / prev)
        f: dict[str, Array] = {"atr20": _rolling(tr, 20, "mean"), "sma5": _rolling(c, 5, "mean"),
                               "rsi2": _rsi(c, 2), "vol20": _rolling(log_ret, 20, "std")}
        for n in (50, 100, 200):
            f[f"sma{n}"] = _rolling(c, n, "mean")
        for n in (10, 20, 55):
            f[f"hh{n}"] = _rolling(h, n, "max", shift=1)
        for n in (5, 10, 20):
            f[f"ll{n}"] = _rolling(low, n, "min", shift=1)
        for k in (1, 3, 7, 14, 28, 56, 91):
            back = np.full(c.shape, np.nan)
            if c.shape[1] > k:                          # a history shorter than k days has no k-day return yet
                back[:, k:] = c[:, :-k]
            f[f"ret{k}"] = c / back - 1
        dollar = c * p.v
        f["dollar30"] = _rolling(dollar, 30, "median")
        f["volume_trend"] = _rolling(dollar, 7, "mean") / _rolling(dollar, 30, "mean") - 1
        f["dist_hh20"] = c / f["hh20"] - 1
        f["dist_hh55"] = c / f["hh55"] - 1
        f["dist_sma50"] = c / f["sma50"] - 1
        f["dist_sma200"] = c / f["sma200"] - 1
        history = np.cumsum(~np.isnan(c), axis=1)
        f["liquid"] = ((f["dollar30"] >= MIN_DOLLAR_VOLUME) & (history >= 120)).astype(np.float64)
        btc = 0 if p.symbols and p.symbols[0] == BTC else None
        on = np.zeros(c.shape[1])
        btc_ret28 = np.full(c.shape[1], np.nan)
        if btc is not None:
            on = (c[btc] > f["sma100"][btc]).astype(np.float64)
            btc_ret28 = f["ret28"][btc]
        f["btc_on"] = np.broadcast_to(on, c.shape).copy()
        f["btc_ret28"] = np.broadcast_to(btc_ret28, c.shape).copy()
        stop = np.clip(STOP_ATR * f["atr20"] / c, STOP_MIN, STOP_MAX)
        f["stop_frac"] = stop
    return f


# ── rules shared by the simulation and PAPER ──────────────────────────────────────────────────────────
def entry_mask(cand: Candidate, p: Panel) -> Mask:
    """Signals at the close of day t (act at the open of t+1), for the TREND and REVERT families."""
    f, c = p.f, p.c
    liquid = f["liquid"] > 0
    regime = (f["btc_on"] > 0) if cand.p("regime") else np.ones(c.shape, dtype=bool)
    with np.errstate(invalid="ignore"):
        if cand.family == "TREND":
            trend = (c > f[f"sma{cand.p('sma')}"]) if cand.p("sma") else np.ones(c.shape, dtype=bool)
            mask = (c > f[f"hh{cand.p('n')}"]) & trend
        elif cand.family == "REVERT":
            mask = (f["rsi2"] < cand.p("rsi")) & (c > f[f"sma{cand.p('sma')}"])
        elif cand.family == "TIMING":
            mask = (c > f[f"sma{cand.p('sma')}"]) & coin_set(cand.p("coins"), p)
        else:
            raise ValueError("XMOM entries are chosen at rebalances")
    return np.asarray(mask & regime & liquid, dtype=bool)


def coin_set(name: str, p: Panel) -> Mask:
    """Which coins a TIMING variant may hold, per day: BTC, BTC and ETH, or the 5 highest median dollar volumes."""
    mask = np.zeros(p.c.shape, dtype=bool)
    if name in ("BTC", "MAJORS2"):
        for s, symbol in enumerate(p.symbols):
            if symbol == BTC or (name == "MAJORS2" and symbol == "ETH-USD"):
                mask[s] = True
        return mask
    volume = np.nan_to_num(p.f["dollar30"], nan=-1.0)
    order = np.argsort(-volume, axis=0, kind="stable")
    ranks = np.empty_like(order)
    np.put_along_axis(ranks, order, np.arange(volume.shape[0])[:, None].repeat(volume.shape[1], axis=1), axis=0)
    return np.asarray((ranks < 5) & (volume > 0), dtype=bool)


def stop_fraction(cand: Candidate, p: Panel, s: int, t: int) -> float:
    """Initial stop distance: 2×ATR clamped to 5-25%, or a fixed 25% catastrophe stop for slow TIMING holds."""
    return STOP_MAX if cand.family == "TIMING" else float(p.f["stop_frac"][s, t])


EXIT_CODES = ("", "LOW_BREAK", "TRAILING_STOP", "MAX_HOLD", "BOUNCED", "REBALANCED_OUT", "BELOW_AVERAGE")


def exit_codes(family: str, params: dict[str, Any], p: Panel, s: int, start: int, end: int, entry_t: int,
               peaks: Array) -> NDArray[np.int64]:
    """Exit decided at each close t in [start, end) for a position entered at the open of ``entry_t`` (it sells at
    the open of t+1). ``peaks[i]`` is the highest close since entry up to t. 0 = hold; else an EXIT_CODES index.
    The one definition used by the simulation (whole spans) and by PAPER (a single day)."""
    f = p.f
    c = p.c[s, start:end]
    held = np.arange(start, end) + 1 - entry_t
    codes = np.zeros(end - start, dtype=np.int64)
    with np.errstate(invalid="ignore"):
        if family == "TREND":
            maxed = held >= params["hold"]
            trail = c < peaks - params["k"] * f["atr20"][s, start:end]
            low = c < f[f"ll{params['x']}"][s, start:end]
            codes = np.where(maxed, 3, codes)
            codes = np.where(trail, 2, codes)
            codes = np.where(low, 1, codes)
        elif family == "REVERT":
            codes = np.where(held >= params["hold"], 3, codes)
            codes = np.where(c > f["sma5"][s, start:end], 4, codes)
        elif family == "MODEL":
            codes = np.where(held >= MODEL_HOLD, 3, codes)
        elif family == "TIMING":
            codes = np.where(held >= params["hold"], 3, codes)
            codes = np.where(c < f[f"sma{params['sma']}"][s, start:end], 6, codes)
    codes[np.isnan(c)] = 0                     # a missing bar never triggers an exit by itself
    return codes


def rebalance_day(p: Panel, t: int, every: int = 1) -> bool:
    """Signals at the close of Sunday trade at Monday's open; ``every`` weeks keeps one Sunday in ``every``
    (counted from 1970-01-04, a Sunday, so the calendar never depends on where the data starts)."""
    day = p.days[t]
    if (day + timedelta(days=1)).weekday() != 0:
        return False
    return ((day - date(1970, 1, 4)).days // 7) % every == 0


def xmom_targets(cand: Candidate, p: Panel, t: int) -> list[int]:
    look = cand.p("look")
    f, c = p.f, p.c
    if cand.p("regime") and not f["btc_on"][0, t] > 0:
        return []
    if t - 1 - look < 0:
        return []
    with np.errstate(invalid="ignore", divide="ignore"):
        run = c[:, t - 1] / c[:, t - 1 - look] - 1                 # skip the latest day
    btc_run = run[0] if p.symbols and p.symbols[0] == BTC else 0.0
    floor = max(0.0, 0.0 if math.isnan(btc_run) else btc_run)
    ranked = [s for s in np.argsort(-np.nan_to_num(run, nan=-np.inf)) if s != 0 or p.symbols[0] != BTC]
    picks = [int(s) for s in ranked if f["liquid"][s, t] > 0 and not math.isnan(run[s]) and run[s] > floor]
    return picks[:cand.p("top")]


# ── simulation ──────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Trade:
    cand: str
    s: int
    entry_t: int              # the open it bought at
    exit_t: int               # the day it sold (at the open, or intraday at the stop)
    net: float                # return after the coin's round-trip cost
    btc: float                # BTC over the same span, no cost (NaN when BTC had no bars)
    reason: str
    open_at_end: bool = False


def _net(entry: float, exit_: float, cost: float) -> float:
    return exit_ * (1 - cost / 2) / (entry * (1 + cost / 2)) - 1


def _btc(p: Panel, entry_t: int, exit_t: int, at_open: bool) -> float:
    if not p.symbols or p.symbols[0] != BTC:
        return math.nan
    start = p.o[0, entry_t]
    end = p.o[0, exit_t] if at_open else p.c[0, exit_t]
    return float(end / start - 1) if start > 0 and not math.isnan(end) else math.nan


def walk(family: str, params: dict[str, Any], p: Panel, s: int, entry_t: int, stop: float, cost: float, label: str,
         *, until: int | None = None, keep: Iterable[int] | None = None) -> Trade:
    """Follow one position from its entry open: intraday stop first, then the close-based exit (sold next open).
    ``keep`` lists XMOM rebalance closes at which the coin stayed a target; any other rebalance close exits it."""
    last = (until if until is not None else len(p.days)) - 1
    entry = float(p.o[s, entry_t])
    kept = set(keep or ())
    every = int(params.get("every", 1))
    if family in ("TREND", "REVERT", "TIMING"):
        span_end = min(last + 1, entry_t + int(params["hold"]) + 1)     # a max-hold exit always falls inside
    elif family == "MODEL":
        span_end = min(last + 1, entry_t + MODEL_HOLD + 1)
    else:
        out_at = next((t for t in range(entry_t, last + 1) if rebalance_day(p, t, every) and t not in kept), None)
        span_end = last + 1 if out_at is None else min(last + 1, out_at + 2)
    closes = p.c[s, entry_t:span_end]
    peaks = np.fmax.accumulate(np.concatenate([[entry], np.nan_to_num(closes, nan=-np.inf)]))[1:]
    with np.errstate(invalid="ignore"):
        stop_hit = np.nonzero(p.low[s, entry_t:span_end] <= stop)[0]
    codes = exit_codes(family, params, p, s, entry_t, span_end, entry_t, peaks)
    if family == "XMOM":
        codes = np.array([5 if rebalance_day(p, t, every) and t not in kept else 0
                          for t in range(entry_t, span_end)], dtype=np.int64)
    signal = np.nonzero(codes)[0]
    first_stop = int(stop_hit[0]) if len(stop_hit) else None
    first_exit = int(signal[0]) if len(signal) else None
    if first_stop is not None and (first_exit is None or first_stop <= first_exit):
        t = entry_t + first_stop
        fill = stop if t == entry_t else min(float(p.o[s, t]), stop)
        return Trade(label, s, entry_t, t, _net(entry, fill, cost), _btc(p, entry_t, t, False), "HARD_STOP")
    if first_exit is not None and entry_t + first_exit + 1 <= last:
        t = entry_t + first_exit + 1
        fill = float(p.o[s, t])
        if not math.isnan(fill):
            reason = EXIT_CODES[int(codes[first_exit])]
            return Trade(label, s, entry_t, t, _net(entry, fill, cost), _btc(p, entry_t, t, True), reason)
    if span_end <= last:                     # the exit day had no bar: sell at the next open that exists
        later = next((u for u in range(span_end, last + 1) if not math.isnan(p.o[s, u])), None)
        if later is not None:
            reason = "REBALANCED_OUT" if family == "XMOM" else "MAX_HOLD"
            return Trade(label, s, entry_t, later, _net(entry, float(p.o[s, later]), cost),
                         _btc(p, entry_t, later, True), reason)
    t = span_end - 1
    while t > entry_t and math.isnan(p.c[s, t]):
        t -= 1
    return Trade(label, s, entry_t, t, _net(entry, float(p.c[s, t]), cost), _btc(p, entry_t, t, False),
                 "OPEN_AT_END", open_at_end=True)


def simulate(cand: Candidate, p: Panel, cost: Array) -> list[Trade]:
    """Every trade the variant would have made over the whole panel, one position per coin at a time."""
    params = dict(cand.params)
    trades: list[Trade] = []
    t_max = len(p.days)
    stop_frac = p.f["stop_frac"]
    if cand.family in ("TREND", "REVERT", "TIMING"):
        mask = entry_mask(cand, p)
        for s in range(len(p.symbols)):
            free = 0
            for t in np.nonzero(mask[s, :t_max - 1])[0]:
                e = int(t) + 1
                if e < free or math.isnan(p.o[s, e]) or math.isnan(stop_frac[s, t]):
                    continue
                trade = walk(cand.family, params, p, s, e, float(p.o[s, e] * (1 - stop_fraction(cand, p, s, int(t)))),
                             float(cost[s]), cand.id)
                trades.append(trade)
                free = trade.exit_t + 1
        return trades
    # XMOM: targets each Sunday close; a holding stays while it is still a target
    holding: dict[int, int] = {}
    rebalances = [t for t in range(t_max - 1) if rebalance_day(p, t, int(params.get("every", 1)))]
    targets_at = {t: xmom_targets(cand, p, t) for t in rebalances}
    for t in rebalances:
        for s in targets_at[t]:
            e = t + 1
            if s in holding and holding[s] > t:
                continue
            if math.isnan(p.o[s, e]) or math.isnan(stop_frac[s, t]):
                continue
            keep = [r for r in rebalances if r > t and s in targets_at[r]]
            trade = walk("XMOM", params, p, s, e, float(p.o[s, e] * (1 - stop_frac[s, t])), float(cost[s]), cand.id,
                         keep=keep)
            trades.append(trade)
            holding[s] = trade.exit_t
    return trades


# ── the model: L2 logistic on daily features, refit at every fold ─────────────────────────────────────
def _model_rows(p: Panel, cost: Array, start: int, end: int) -> tuple[Array, Array]:
    """Features at closes t in [start, end) with labels known by end: did buying the next open and selling
    MODEL_HOLD days later beat the coin's round-trip cost?"""
    xs, ys = [], []
    stack = np.stack([p.f[name] for name in MODEL_FEATURES], axis=2)       # (S, T, k)
    for t in range(max(start, 1), end):
        e, x = t + 1, t + 1 + MODEL_HOLD
        if x >= len(p.days):
            break
        rows = stack[:, t, :]
        with np.errstate(invalid="ignore", divide="ignore"):
            net = p.o[:, x] * (1 - cost / 2) / (p.o[:, e] * (1 + cost / 2)) - 1
        ok = (p.f["liquid"][:, t] > 0) & ~np.isnan(rows).any(axis=1) & ~np.isnan(net)
        xs.append(rows[ok])
        ys.append((net[ok] > 0).astype(np.float64))
    if not xs:
        return np.empty((0, len(MODEL_FEATURES))), np.empty(0)
    return np.concatenate(xs), np.concatenate(ys)


@dataclass(frozen=True)
class Model:
    weights: tuple[float, ...]
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    low: tuple[float, ...]
    high: tuple[float, ...]
    rows: int

    def predict(self, x: Array) -> Array:
        z = (np.clip(x, self.low, self.high) - np.array(self.mean)) / np.array(self.scale)
        logits = self.weights[0] + z @ np.array(self.weights[1:])
        return np.asarray(1 / (1 + np.exp(-np.clip(logits, -30, 30))), dtype=np.float64)


def train_model(p: Panel, cost: Array, until: int) -> Model | None:
    """Fit on outcomes known before the close of day ``until`` (labels end at most at that day's open)."""
    x, y = _model_rows(p, cost, until - MODEL_TRAIN_DAYS, until - MODEL_HOLD - 1)
    if len(y) < 500 or y.min() == y.max():
        return None
    low, high = np.quantile(x, 0.01, axis=0), np.quantile(x, 0.99, axis=0)
    fitted = fit_logistic(np.clip(x, low, high), y, l2=1.0)
    return Model(tuple(fitted.weights), tuple(fitted.mean), tuple(fitted.scale), tuple(low), tuple(high), len(y))


def model_picks(model: Model, p: Panel, t: int) -> list[int]:
    rows = np.stack([p.f[name][:, t] for name in MODEL_FEATURES], axis=1)
    ok = (p.f["liquid"][:, t] > 0) & ~np.isnan(rows).any(axis=1)
    if not ok.any():
        return []
    prob = np.full(len(p.symbols), -1.0)
    prob[ok] = model.predict(rows[ok])
    order = [int(s) for s in np.argsort(-prob) if prob[s] >= MODEL_THRESHOLD]
    return order[:MODEL_TOP]


def model_trades(model: Model, p: Panel, cost: Array, start: int, end: int, label: str) -> list[Trade]:
    out = []
    for t in range(start, min(end, len(p.days) - 1)):
        if not rebalance_day(p, t):
            continue
        for s in model_picks(model, p, t):
            e = t + 1
            if math.isnan(p.o[s, e]) or math.isnan(p.f["stop_frac"][s, t]):
                continue
            out.append(walk("MODEL", {}, p, s, e, float(p.o[s, e] * (1 - p.f["stop_frac"][s, t])), float(cost[s]),
                            label))
    return out


# ── walk-forward judging ────────────────────────────────────────────────────────────────────────────
def _lcb(nets: list[float]) -> float:
    """Mean minus one standard error (per trade): used to report how steady an edge is."""
    if len(nets) < 2:
        return -math.inf
    arr = np.array(nets)
    return float(arr.mean() - arr.std(ddof=1) / math.sqrt(len(arr)))


def _score(nets: list[float]) -> float:
    """What the variant earned over the window in tickets, minus one standard error of that total: a variant
    with few big wins and one with many small ones compete fairly, and noise is discounted."""
    arr = np.array(nets)
    return float(arr.sum() - math.sqrt(len(arr)) * (arr.std(ddof=1) if len(arr) > 1 else abs(arr[0])))


def rank(trades_by_cand: dict[str, list[Trade]], start: int, end: int) -> list[tuple[str, float, int]]:
    """Variants scored on trades entered in [start, end) whose outcome was known by the close of end-1."""
    scored = []
    for cid, trades in trades_by_cand.items():
        nets = [t.net for t in trades if start <= t.entry_t < end and t.exit_t <= end - 1 and not t.open_at_end]
        if len(nets) >= MIN_TRAIN_TRADES:
            scored.append((cid, _score(nets), len(nets)))
    return sorted(scored, key=lambda r: (-r[1], r[0]))


def _capped(trades: list[Trade]) -> list[Trade]:
    """At most MAX_POSITIONS open at once and one position per coin, first come first served (as in PAPER)."""
    kept: list[Trade] = []
    for t in sorted(trades, key=lambda x: (x.entry_t, x.cand, x.s)):
        live = [k for k in kept if k.exit_t > t.entry_t or (k.open_at_end and k.exit_t >= t.entry_t)]
        if len(live) < MAX_POSITIONS and all(k.s != t.s for k in live):
            kept.append(t)
    return kept


def fold_starts(p: Panel) -> list[int]:
    return list(range(WARMUP_DAYS + TRAIN_DAYS, len(p.days), TEST_DAYS))


def _p_positive(values: list[float], seed: int) -> float | None:
    """One-sided stationary-bootstrap p-value for a positive mean (share of resampled means <= 0)."""
    if len(values) < 10:
        return None
    means = stationary_bootstrap_means(np.array(values), resamples=RESAMPLES, seed=seed)
    return max(float(np.mean(means <= 0)), 1 / RESAMPLES)


def summarize(trades: list[Trade], p: Panel, folds: int, seed: int, *, ticket_usd: float = 50.0) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda t: (t.entry_t, t.s))
    nets = [t.net for t in ordered]
    excess = [t.net - t.btc for t in ordered if not math.isnan(t.btc)]
    curve, btc_curve, points = 0.0, 0.0, []
    for t in sorted(ordered, key=lambda x: x.exit_t):
        curve += ticket_usd * t.net
        btc_curve += 0 if math.isnan(t.btc) else ticket_usd * t.btc
        points.append([p.days[t.exit_t].isoformat(), round(curve, 2), round(btc_curve, 2)])
    hold_days = [max(t.exit_t - t.entry_t, 1) for t in ordered]
    return {
        "folds": folds, "trades": len(ordered), "wins": sum(1 for n in nets if n > 0),
        "mean_net": float(np.mean(nets)) if nets else None, "median_net": float(np.median(nets)) if nets else None,
        "lcb_net": _lcb(nets) if len(nets) >= 2 else None,
        "mean_excess_vs_btc": float(np.mean(excess)) if excess else None,
        "pnl_per_dollar_year": float(sum(nets) * 365 / sum(hold_days)) if nets else None,
        "p_profit": _p_positive(nets, seed), "p_beats_btc": _p_positive(excess, seed + 1),
        "curve": points[-400:], "ticket_usd": ticket_usd,
        "curve_kind": "fixed-ticket cumulative P&L; not an investable portfolio equity curve",
    }


@dataclass
class LabResult:
    generated_at: str
    data_end: str
    symbols: int
    days: int
    candidates: int
    folds: list[dict[str, Any]]
    procedures: dict[str, dict[str, Any]]
    promoted: str | None
    selection: dict[str, Any]
    leaderboard: list[dict[str, Any]]
    cost_model: dict[str, str]
    capital_profile: dict[str, str]

    def as_json(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("generated_at", "data_end", "symbols", "days", "candidates", "folds",
                                              "procedures", "promoted", "selection", "leaderboard", "cost_model",
                                              "capital_profile")}


def run_lab(p: Panel, cost: Array, *, now: datetime, cost_sources: dict[str, str] | None = None,
            seed: int = 0, reference_capital_usd: float = 500.0, ticket_fraction: float = 0.10) -> LabResult:
    """Walk forward over the whole panel, judge the three procedures on unseen months, and choose today's selection."""
    if reference_capital_usd <= 0 or not 0 < ticket_fraction <= 1:
        raise ValueError("positive reference capital and ticket fraction in (0, 1] required")
    ticket_usd = reference_capital_usd * ticket_fraction
    cands = {c.id: c for c in candidates()}
    trades = {cid: simulate(c, p, cost) for cid, c in cands.items()}
    oos: dict[str, list[Trade]] = {name: [] for name in PROCEDURES}
    folds = []
    for a in fold_starts(p):
        end = min(a + TEST_DAYS, len(p.days))
        ranked = rank(trades, a - TRAIN_DAYS, a)
        chosen = [r[0] for r in ranked[:3]]
        record: dict[str, Any] = {"start": p.days[a].isoformat(), "end": p.days[end - 1].isoformat(),
                                  "best": chosen[:1], "top3": chosen}
        if chosen:
            oos["LEARN_BEST"] += _capped([t for t in trades[chosen[0]] if a <= t.entry_t < end])
            oos["LEARN_TOP3"] += _capped([t for cid in chosen for t in trades[cid] if a <= t.entry_t < end])
        model = train_model(p, cost, a)
        record["model_rows"] = 0 if model is None else model.rows
        if model is not None:
            oos["LEARN_MODEL"] += _capped(model_trades(model, p, cost, a, end, "MODEL"))
        folds.append(record)
    procedures = {name: summarize(oos[name], p, len(folds), seed + 10 * i, ticket_usd=ticket_usd)
                  for i, name in enumerate(PROCEDURES)}
    names = [n for n in PROCEDURES if procedures[n]["p_profit"] is not None and procedures[n]["p_beats_btc"] is not None]
    adj_profit = holm([procedures[n]["p_profit"] for n in names])
    adj_btc = holm([procedures[n]["p_beats_btc"] for n in names])
    for n, pp, pb in zip(names, adj_profit, adj_btc, strict=True):
        procedures[n]["holm_p_profit"], procedures[n]["holm_p_beats_btc"] = pp, pb
    for n, row in procedures.items():
        failures = []
        if row["folds"] < MIN_FOLDS:
            failures.append(f"{row['folds']} of {MIN_FOLDS} unseen months")
        if row["trades"] < MIN_OOS_TRADES:
            failures.append(f"{row['trades']} of {MIN_OOS_TRADES} unseen trades")
        if row.get("holm_p_profit") is None or row["holm_p_profit"] >= ALPHA_PROFIT:
            failures.append("not reliably profitable after costs on unseen months")
        if row.get("holm_p_beats_btc") is None or row["holm_p_beats_btc"] >= ALPHA_BTC:
            failures.append("did not reliably beat holding BTC over the same days")
        row["passed"], row["failures"] = not failures, failures
        row["label"] = {"LEARN_BEST": "Best single rule each month", "LEARN_TOP3": "Best three rules each month",
                        "LEARN_MODEL": "Statistical model, retrained monthly"}[n]
    passing = [n for n in PROCEDURES if procedures[n]["passed"]]
    promoted = max(passing, key=lambda n: procedures[n]["lcb_net"] or -math.inf) if passing else None
    # today's choice, made exactly the way each unseen month was
    now_t = len(p.days)
    ranked_now = rank(trades, now_t - TRAIN_DAYS, now_t)
    model_now = train_model(p, cost, now_t)
    selection = {"best": [r[0] for r in ranked_now[:1]], "top3": [r[0] for r in ranked_now[:3]],
                 "model": None if model_now is None else {
                     "weights": model_now.weights, "mean": model_now.mean, "scale": model_now.scale,
                     "low": model_now.low, "high": model_now.high, "rows": model_now.rows}}
    leaderboard = [{"id": cid, "describe": cands[cid].describe(), "score": score, "trades": n,
                    "mean_net": float(np.mean([t.net for t in trades[cid] if now_t - TRAIN_DAYS <= t.entry_t
                                               and t.exit_t <= now_t - 1 and not t.open_at_end]))}
                   for cid, score, n in ranked_now[:12]]
    return LabResult(now.isoformat(), p.days[-1].isoformat(), len(p.symbols), len(p.days), len(cands), folds,
                     procedures, promoted, selection, leaderboard, cost_sources or {},
                     {"reference_capital_usd": str(reference_capital_usd), "ticket_usd": str(ticket_usd),
                      "ticket_fraction": str(ticket_fraction), "kind": "fixed-ticket historical illustration"})


def selection_sha256(result: LabResult) -> str:
    """Versions a learned PAPER position: the lab definition plus exactly what was selected."""
    return sha256_hex(canonical_json({"lab": lab_sha256(), "promoted": result.promoted,
                                      "selection": result.selection, "data_end": result.data_end}))


def candidate_by_id(cid: str) -> Candidate:
    for c in candidates():
        if c.id == cid:
            return c
    raise KeyError(cid)


def model_from(selection: dict[str, Any]) -> Model | None:
    m = selection.get("model")
    if not m:
        return None
    return Model(tuple(m["weights"]), tuple(m["mean"]), tuple(m["scale"]), tuple(m["low"]), tuple(m["high"]),
                 int(m["rows"]))


def day_index(p: Panel, day: date) -> int | None:
    return (day - p.days[0]).days if p.days and p.days[0] <= day <= p.days[-1] else None

