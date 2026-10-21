"""Proof: does each list and each desk do what it says? (docs/STOCKS_METHOD.md)

Two records, never mixed:

* **live**: every day's lists and desk scores, appended to stocks-research.db once (``desk.stocks.daily``) and
  scored as their forward returns arrive. This is the record that counts.
* **rebuilt**: the same code run as of each past month's last session on only what was public that day
  (``Analyzer.build`` is point in time), kept in ``stk_rebuild_months``. It starts from today's listings, so companies
  delisted before collection began are missing (survivorship bias: it flatters lists that buy and hurts lists that
  avoid). Crowd and options data exist only from the first day of collection, so those desks and the lists built on
  them have a live record only.

A list is bought at the next session's open and held 5, 21, 63 or 252 sessions, sold at that session's close (a stock
that stops trading is sold at its last close), price-only, against SPY and the stock's sector fund over the same
sessions. A list that claims to beat the market is charged a 0.2% round trip; a list of stocks to avoid is judged on
falling behind the market. Each claim gets a one-sided stationary-bootstrap p-value (the bootstrap keeps the overlap
of daily lists honest) and the claims are Holm-adjusted together. Desks are judged by information coefficient (the
rank correlation of the score with the next month's return), by the return spread between their top and bottom
tenth and, for the risk desk, by the spread in the next quarter's worst drawdown.

Committee weights are learned walk-forward: each month's weights come only from months whose returns were complete
before it, and they replace the defaults only if, on months they never saw, they ranked stocks better.
"""

from __future__ import annotations

import math
import warnings
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from typing import Any

import numpy as np

from desk.core.hashing import canonical_json, sha256_hex
from desk.stats.core import holm, mean_interval, stationary_bootstrap_means
from desk.stocks import calendar as cal
from desk.stocks import desks as dk
from desk.stocks.analysis import Analyzer
from desk.stocks.lists import LISTS
from desk.stocks.prices import Panel, last_valid
from desk.stocks.repo import StocksMarket, StocksResearch
from desk.stocks.sectors import sector
from desk.stocks.universe import SECTOR_FUNDS, research_listings

Array = np.ndarray

VERSION = 1
HORIZONS = {"1w": 5, "1m": 21, "3m": 63, "12m": 252}
PRIMARY = "1m"
DRAWDOWN_HORIZON = "3m"
COST_ROUND_TRIP = 0.002          # spread and slippage for a liquid US stock bought and sold once, commission-free
MIN_INDEPENDENT = 12             # independent periods (non-overlapping months) before a list or desk is graded
RESAMPLES = 2000
SEED = 0
REBUILD_MIN_HISTORY = 252        # sessions of prices before a rebuilt month (a year, for the one-year features)
MONTH_SESSIONS = 21
MIN_CROSS_SECTION = 10
MIN_TRAIN_MONTHS = 6
SHRINK_MONTHS = 12               # learned weights are shrunk toward the defaults by n / (n + 12) months of evidence
LIVE_DESK_STEP = 5               # live desk scores are judged weekly (every fifth recorded session)
LIVE_ONLY_DESKS = ("crowd", "options")
PROVEN_LIVE_WEIGHT = 0.4
DESK_CLAIMS = {d: ("drawdown" if d == "risk" else "return") for d in dk.DESKS}
TESTED_CLAIMS = ("outperform", "underperform")


def content() -> dict[str, Any]:
    return {"version": VERSION, "horizons": HORIZONS, "primary": PRIMARY, "drawdown_horizon": DRAWDOWN_HORIZON,
            "cost_round_trip": COST_ROUND_TRIP, "min_independent": MIN_INDEPENDENT, "resamples": RESAMPLES, "seed": SEED,
            "rebuild_min_history": REBUILD_MIN_HISTORY, "min_train_months": MIN_TRAIN_MONTHS,
            "shrink_months": SHRINK_MONTHS, "live_desk_step": LIVE_DESK_STEP, "desk_claims": DESK_CLAIMS,
            "proven_live_weight": PROVEN_LIVE_WEIGHT, "entry": "next session open",
            "exit": "close of the horizon's last session", "returns": "price only"}


def rebuild_sha256() -> str:
    """Everything a rebuilt month depends on: the desks and the lists."""
    return sha256_hex(canonical_json({"desks": dk.method_content(), "lists": [s.content() for s in LISTS]}))


def method_sha256() -> str:
    return sha256_hex(canonical_json({"rebuild": rebuild_sha256(), "validation": content()}))


# ── statistics ───────────────────────────────────────────────────────────────────────────────────
def average_ranks(x: Array) -> Array:
    """Ranks from 0 with ties sharing their average rank."""
    _, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
    starts = np.cumsum(counts) - counts
    return np.asarray((starts + (counts - 1) / 2)[inverse], dtype=float)


def spearman(x: Array, y: Array) -> float | None:
    ok = ~(np.isnan(x) | np.isnan(y))
    if ok.sum() < MIN_CROSS_SECTION:
        return None
    rx, ry = average_ranks(x[ok]), average_ranks(y[ok])
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def one_sided_p(x: Array) -> float | None:
    """P-value for "the mean is above zero": the share of centred stationary-bootstrap means at least as large as the
    observed mean (with the +1 correction)."""
    if len(x) < 3 or float(np.ptp(x)) == 0:
        return None
    means = stationary_bootstrap_means(x, resamples=RESAMPLES, seed=SEED)
    centred = means - x.mean()
    return float((1 + np.sum(centred >= x.mean())) / (RESAMPLES + 1))


def summarize(x: Sequence[float], horizon: int, step: int) -> dict[str, Any]:
    """Mean, 95% interval and one-sided p of a per-observation series; ``independent`` counts non-overlapping
    periods (observations ``step`` sessions apart, each spanning ``horizon`` sessions)."""
    a = np.asarray(x, dtype=float)
    n = len(a)
    independent = float(n) if step >= horizon else n * step / horizon
    out: dict[str, Any] = {"n": n, "independent": round(independent, 1), "mean": None, "ci": None, "p": None,
                           "p_holm": None}
    if n == 0:
        return out
    out["mean"] = float(a.mean())
    if n >= 3 and float(np.ptp(a)) > 0:
        interval = mean_interval(a, resamples=RESAMPLES, seed=SEED)
        out["ci"] = [interval.lower, interval.upper]
        out["p"] = one_sided_p(a)
    return out


def _claimed(excess: float, claim: str) -> float:
    """The per-observation number a claim is tested on (positive = the claim held)."""
    if claim == "outperform":
        return excess - COST_ROUND_TRIP
    if claim == "underperform":
        return -excess
    return excess


def _hit(excess: float, claim: str) -> bool:
    if claim == "outperform":
        return excess > COST_ROUND_TRIP
    if claim == "underperform":
        return excess < 0
    return excess > 0


# ── forward returns ──────────────────────────────────────────────────────────────────────────────
class Forward:
    """What happened after a decision day: every panel symbol's return from the next session's open to the close of
    the horizon's last session (cached per window). NaN when the stock didn't open that session."""

    def __init__(self, panel: Panel) -> None:
        self.panel = panel
        self._returns: dict[tuple[int, int], Array] = {}
        self._drawdowns: dict[tuple[int, int], Array] = {}

    def window(self, asof: date, horizon: int) -> tuple[int, int] | None:
        t = self.panel.day_index(asof)
        if t < 0:
            return None
        entry, exit_ = t + 1, t + horizon
        return None if exit_ >= len(self.panel.days) else (entry, exit_)

    def returns(self, asof: date, horizon: int) -> Array | None:
        w = self.window(asof, horizon)
        if w is None:
            return None
        if w not in self._returns:
            entry, exit_ = w
            last = last_valid(self.panel.close, exit_, max_gap=exit_ - entry)
            with np.errstate(divide="ignore", invalid="ignore"):
                self._returns[w] = last / self.panel.open[:, entry] - 1
        return self._returns[w]

    def bench(self, asof: date, horizon: int, symbol: str = "SPY") -> float | None:
        r = self.returns(asof, horizon)
        i = self.panel.index.get(symbol)
        if r is None:
            return None
        if i is None or math.isnan(r[i]):
            return None if symbol == "SPY" else self.bench(asof, horizon, "SPY")
        return float(r[i])

    def drawdowns(self, asof: date, horizon: int) -> Array | None:
        """Worst decline from a running peak (starting at the entry open) through the window's closes."""
        w = self.window(asof, horizon)
        if w is None:
            return None
        if w not in self._drawdowns:
            entry, exit_ = w
            path = np.concatenate([self.panel.open[:, entry:entry + 1], self.panel.close[:, entry:exit_ + 1]], axis=1)
            peaks = np.fmax.accumulate(path, axis=1)
            with np.errstate(divide="ignore", invalid="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", category=RuntimeWarning)      # all-NaN rows stay NaN
                worst = np.nanmin(path / peaks - 1, axis=1)
            worst[np.isnan(path[:, 0])] = np.nan
            self._drawdowns[w] = worst
        return self._drawdowns[w]

    def span(self, start: date, end: date) -> Array | None:
        """Open after ``start`` to open after ``end`` (a monthly rebalance); a stock gone by then exits at its last
        close."""
        a, b = self.window(start, 1), self.window(end, 1)
        if a is None or b is None or b[0] <= a[0]:
            return None
        exit_px = self.panel.open[:, b[0]].copy()
        missing = np.isnan(exit_px)
        exit_px[missing] = last_valid(self.panel.close, b[0] - 1, max_gap=b[0] - 1 - a[0])[missing]
        with np.errstate(divide="ignore", invalid="ignore"):
            out: Array = exit_px / self.panel.open[:, a[0]] - 1
        return out


SectorOf = Callable[[date, str], str]


# ── lists ────────────────────────────────────────────────────────────────────────────────────────
def list_record(fw: Forward, observations: Sequence[tuple[date, Sequence[str]]], claim: str, sector_of: SectorOf, *,
                step: int) -> dict[str, Any]:
    """Each horizon's record for one list: the equal-weight return of each observation's picks against SPY and their
    sector funds, the claim's test series, and the share of picks that did what the list claims."""
    out: dict[str, Any] = {}
    for name, h in HORIZONS.items():
        tested: list[float] = []
        rets: list[float] = []
        vs_spy: list[float] = []
        vs_sector: list[float] = []
        hits = picks = 0
        for asof, symbols in observations:
            r, spy = fw.returns(asof, h), fw.bench(asof, h)
            if r is None or spy is None:
                continue
            pr: list[float] = []
            ps: list[float] = []
            for s in symbols:
                i = fw.panel.index.get(s)
                if i is None or math.isnan(r[i]):
                    continue
                x = float(r[i])
                fund = fw.bench(asof, h, SECTOR_FUNDS.get(sector_of(asof, s), "SPY"))
                pr.append(x)
                ps.append(x - (spy if fund is None else fund))
                hits += _hit(x - spy, claim)
                picks += 1
            if not pr:
                continue
            mean = float(np.mean(pr))
            rets.append(mean)
            vs_spy.append(mean - spy)
            vs_sector.append(float(np.mean(ps)))
            tested.append(_claimed(mean - spy, claim))
        stat = summarize(tested, h, step)
        stat.update({"mean_return": _mean(rets), "mean_excess_spy": _mean(vs_spy), "mean_excess_sector": _mean(vs_sector),
                     "hit_rate": None if picks == 0 else hits / picks, "picks": picks})
        out[name] = stat
    return out


def growth_curve(fw: Forward, monthly: Sequence[tuple[date, Sequence[str]]], claim: str) -> list[dict[str, Any]]:
    """Growth of $1 holding each month's list until the next month's (equal weight, rebalanced at the open after each
    month's end; a list that claims to beat the market pays the round trip every month) against SPY."""
    points: list[dict[str, Any]] = []
    value = bench = 1.0
    for (start, symbols), (end, _) in pairwise(monthly):
        r = fw.span(start, end)
        if r is None:
            break
        spy_i = fw.panel.index.get("SPY")
        picks = [float(r[i]) for s in symbols if (i := fw.panel.index.get(s)) is not None and not math.isnan(r[i])]
        if not points:
            points.append({"asof": start.isoformat(), "list": 1.0, "spy": 1.0})
        month = float(np.mean(picks)) if picks else 0.0
        value *= 1 + month - (COST_ROUND_TRIP if claim == "outperform" and picks else 0.0)
        if spy_i is not None and not math.isnan(r[spy_i]):
            bench *= 1 + float(r[spy_i])
        points.append({"asof": end.isoformat(), "list": round(value, 4), "spy": round(bench, 4)})
    return points


def month_sample(observations: Sequence[tuple[date, Sequence[str]]]) -> list[tuple[date, Sequence[str]]]:
    """Each calendar month's last observation."""
    out: dict[tuple[int, int], tuple[date, Sequence[str]]] = {}
    for d, syms in observations:
        out[(d.year, d.month)] = (d, syms)
    return [out[k] for k in sorted(out)]


# ── desks ────────────────────────────────────────────────────────────────────────────────────────
def _extremes(scores: Array, values: Array) -> tuple[float, float]:
    """Mean of ``values`` for the top and bottom tenth by score (fifth below 50 names)."""
    order = np.argsort(scores, kind="mergesort")
    k = max(1, len(order) // (10 if len(order) >= 50 else 5))
    return float(values[order[-k:]].mean()), float(values[order[:k]].mean())


def desk_record(fw: Forward, observations: Sequence[tuple[date, dict[str, float]]], claim: str, *,
                step: int) -> dict[str, Any]:
    """A desk's record: information coefficient per observation, top-minus-bottom spread, the share of observations
    where its top tenth beat SPY after costs, and (risk) the next quarter's drawdown spread."""
    h = HORIZONS[PRIMARY]
    dd_h = HORIZONS[DRAWDOWN_HORIZON]
    ics: list[float] = []
    spreads: list[float] = []
    dd_spreads: list[float] = []
    beat = counted = 0
    series: list[dict[str, Any]] = []
    for asof, scores in observations:
        r, spy = fw.returns(asof, h), fw.bench(asof, h)
        if r is None or spy is None:
            continue
        syms = [s for s, v in scores.items() if v is not None and s in fw.panel.index]
        idx = np.array([fw.panel.index[s] for s in syms], dtype=int)
        sc = np.array([float(scores[s]) for s in syms])
        rr = r[idx] if len(idx) else np.array([])
        ok = ~np.isnan(rr)
        ic = spearman(sc, rr)
        if ic is None:
            continue
        top, bottom = _extremes(sc[ok], rr[ok])
        ics.append(ic)
        spreads.append(top - bottom)
        counted += 1
        beat += top - spy > COST_ROUND_TRIP
        point: dict[str, Any] = {"asof": asof.isoformat(), "ic": round(ic, 4), "spread": round(top - bottom, 4)}
        if claim == "drawdown":
            dd = fw.drawdowns(asof, dd_h)
            if dd is not None:
                d = dd[idx]
                good = ~np.isnan(d)
                if good.sum() >= MIN_CROSS_SECTION:
                    top_dd, bottom_dd = _extremes(sc[good], d[good])
                    dd_spreads.append(top_dd - bottom_dd)
                    point["drawdown_spread"] = round(top_dd - bottom_dd, 4)
        series.append(point)
    tested = dd_spreads if claim == "drawdown" else ics
    stat = summarize(tested, dd_h if claim == "drawdown" else h, step)
    stat.update({"ic": summarize(ics, h, step), "spread": _mean(spreads), "top_beat_spy": None if counted == 0 else
                 beat / counted, "drawdown_spread": _mean(dd_spreads), "series": series[-60:]})
    return stat


# ── the rebuild ──────────────────────────────────────────────────────────────────────────────────
def rebuild_days(panel: Panel, *, min_history: int = REBUILD_MIN_HISTORY) -> list[date]:
    """Each month's last session with at least ``min_history`` sessions of prices before it."""
    return [d for t, d in enumerate(panel.days) if t >= min_history and cal.next_trading_day(d).month != d.month]


@dataclass
class Month:
    asof: date
    universe: int
    sectors: dict[str, str]
    scores: dict[str, dict[str, tuple[float, float]]]       # desk -> symbol -> (score, confidence)
    lists: dict[str, list[str]]


def _encode(day: Any) -> dict[str, Any]:
    rows: dict[str, list[Any]] = {}
    for s in day.ctx.symbols:
        row: list[Any] = [day.ctx.sectors.get(s, "Other")]
        for d in dk.DESKS:
            r = day.results[d].get(s)
            row += [None, None] if r is None or r.score is None else [round(r.score, 2), round(r.confidence, 3)]
        rows[s] = row
    return {"desks": list(dk.DESKS), "rows": rows}


def _decode(asof: date, universe: int, scores: dict[str, Any], lists: dict[str, list[str]]) -> Month:
    desks = scores["desks"]
    out: dict[str, dict[str, tuple[float, float]]] = {d: {} for d in desks}
    sectors: dict[str, str] = {}
    for s, row in scores["rows"].items():
        sectors[s] = row[0]
        for k, d in enumerate(desks):
            score, conf = row[1 + 2 * k], row[2 + 2 * k]
            if score is not None:
                out[d][s] = (float(score), float(conf))
    return Month(asof, universe, sectors, out, lists)


def rebuild(analyzer: Analyzer, panel: Panel, *, now: datetime, force: bool = False,
            min_history: int = REBUILD_MIN_HISTORY, progress: Callable[[date, int, int], None] | None = None) -> list[date]:
    """Rebuild every month not yet rebuilt under today's methodology (all of them with ``force``)."""
    key = rebuild_sha256()
    done = set(analyzer.research.rebuilt_days(key))
    todo = [d for d in rebuild_days(panel, min_history=min_history) if force or d not in done]
    for k, asof in enumerate(todo):
        day = analyzer.build(panel, asof, now=now, crowd_and_options=False, weights=dict(dk.DEFAULT_WEIGHTS))
        lists = {spec.id: [r["symbol"] for r in spec.select(day)] for spec in LISTS if spec.history}
        analyzer.research.save_rebuild(asof, key, _encode(day), lists, day.universe_size, now)
        if progress is not None:
            progress(asof, k + 1, len(todo))
    return todo


def months(research: StocksResearch) -> list[Month]:
    return [_decode(d, universe, scores, lists) for d, universe, scores, lists in research.rebuilds(rebuild_sha256())]


# ── committee weights, learned walk-forward ──────────────────────────────────────────────────────
LEARNABLE = tuple(d for d in dk.DESKS if d not in LIVE_ONLY_DESKS)


def fit(ic_rows: Sequence[dict[str, float | None]]) -> dict[str, float]:
    """Weights in proportion to each desk's average positive IC, shrunk toward the defaults by n / (n + 12)."""
    means = {d: _mean([r.get(d) for r in ic_rows]) or 0.0 for d in LEARNABLE}
    positive = {d: max(0.0, m) for d, m in means.items()}
    mass = sum(positive.values())
    total = sum(dk.DEFAULT_WEIGHTS[d] for d in LEARNABLE)
    lam = len(ic_rows) / (len(ic_rows) + SHRINK_MONTHS)
    out = dict(dk.DEFAULT_WEIGHTS)
    for d in LEARNABLE:
        learned = positive[d] / mass * total if mass > 0 else dk.DEFAULT_WEIGHTS[d]
        out[d] = round((1 - lam) * dk.DEFAULT_WEIGHTS[d] + lam * learned, 3)
    return out


def committee_scores(month: Month, weights: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in month.sectors:
        results: dict[str, dk.DeskResult | None] = {}
        for d, w in weights.items():
            v = month.scores.get(d, {}).get(s)
            if w > 0 and v is not None:
                results[d] = dk.DeskResult(v[0], v[1], "", [])
        score = dk.blend(results, weights)
        if score is not None:
            out[s] = score
    return out


def _ic_of(fw: Forward, asof: date, scores: dict[str, float]) -> float | None:
    r = fw.returns(asof, HORIZONS[PRIMARY])
    if r is None:
        return None
    syms = [s for s in scores if s in fw.panel.index]
    return spearman(np.array([scores[s] for s in syms]), r[[fw.panel.index[s] for s in syms]])


def learn(fw: Forward, history: Sequence[Month]) -> dict[str, Any]:
    h = HORIZONS[PRIMARY]
    rows: list[tuple[Month, date, dict[str, float | None]]] = []
    for m in history:
        w = fw.window(m.asof, h)
        if w is None:
            continue
        ics = {d: _ic_of(fw, m.asof, {s: v[0] for s, v in m.scores.get(d, {}).items()}) for d in LEARNABLE}
        rows.append((m, fw.panel.days[w[1]], ics))
    oos: list[dict[str, Any]] = []
    for k, (m, _, _) in enumerate(rows):
        train = [ics for prior, ended, ics in rows[:k] if ended < m.asof]       # labels complete before this month
        if len(train) < MIN_TRAIN_MONTHS:
            continue
        weights = fit(train)
        learned, default = (_ic_of(fw, m.asof, committee_scores(m, w)) for w in (weights, dict(dk.DEFAULT_WEIGHTS)))
        if learned is not None and default is not None:
            oos.append({"asof": m.asof.isoformat(), "learned": round(learned, 4), "default": round(default, 4),
                        "train_months": len(train)})
    diffs = [o["learned"] - o["default"] for o in oos]
    candidate = fit([ics for _, _, ics in rows]) if len(rows) >= MIN_TRAIN_MONTHS else None
    adopted = candidate is not None and len(diffs) >= MIN_TRAIN_MONTHS and float(np.mean(diffs)) > 0
    if candidate is None:
        why = f"Not enough rebuilt months with a complete month after them ({len(rows)} of {MIN_TRAIN_MONTHS})"
    elif len(diffs) < MIN_TRAIN_MONTHS:
        why = f"Too few unseen months to compare on ({len(diffs)} of {MIN_TRAIN_MONTHS}); the defaults stay"
    elif adopted:
        why = (f"On {len(diffs)} months they never saw, learned weights ranked stocks better than the defaults "
               f"(IC {np.mean([o['learned'] for o in oos]):+.3f} vs {np.mean([o['default'] for o in oos]):+.3f})")
    else:
        why = (f"On {len(diffs)} unseen months the learned weights did not beat the defaults "
               f"(IC {np.mean([o['learned'] for o in oos]):+.3f} vs {np.mean([o['default'] for o in oos]):+.3f}); "
               "the defaults stay")
    desk_ic = {d: _mean([ics[d] for _, _, ics in rows if ics[d] is not None]) for d in LEARNABLE}
    return {"months": len(rows), "oos": oos, "oos_months": len(diffs), "learned_ic": _mean([o["learned"] for o in oos]),
            "default_ic": _mean([o["default"] for o in oos]), "p_better": one_sided_p(np.array(diffs)) if diffs else None,
            "candidate": candidate, "adopted": adopted, "why": why, "desk_ic": desk_ic}


# ── options: what was priced vs what happened ────────────────────────────────────────────────────
def options_truth(market: StocksMarket, fw: Forward, cheap_iv: Sequence[tuple[date, Sequence[str]]] = ()) -> dict[str, Any]:
    """Implied volatility against the next month's realized volatility, and each first expiry's expected move against
    the actual move to expiry, for every stored option summary whose future has arrived."""
    panel = fw.panel
    month = HORIZONS[PRIMARY]
    iv_rows: list[tuple[str, date, float, float]] = []
    moves: list[tuple[str, date, float, float]] = []
    for sym, d, summary in market.option_daily_all():
        i, t = panel.index.get(sym), panel.day_index(d)
        if i is None or t < 0 or panel.days[t] != d:
            continue
        iv = summary.get("iv30")
        if iv and t + month < len(panel.days):
            closes = panel.close[i, t:t + month + 1]
            if not np.isnan(closes).any() and (closes > 0).all():
                rv = float(np.std(np.diff(np.log(closes)), ddof=1) * math.sqrt(252))
                iv_rows.append((sym, d, float(iv), rv))
        first = next((e for e in summary.get("expiries", []) if e.get("expected_move") and (e.get("days") or 0) >= 1), None)
        if first is not None:
            expiry = date.fromisoformat(first["expiration"])
            j = panel.day_index(expiry)
            if expiry <= panel.days[-1] and j > t and not math.isnan(panel.close[i, j]) and panel.close[i, t] > 0:
                moves.append((sym, d, float(first["expected_move"]), abs(float(panel.close[i, j] / panel.close[i, t] - 1))))
    sym_moves: dict[str, list[tuple[float, float]]] = defaultdict(list)
    sym_ratios: dict[str, list[float]] = defaultdict(list)
    for sym, _, expected, actual in moves:
        sym_moves[sym].append((expected, actual))
    for sym, _, iv, rv in iv_rows:
        sym_ratios[sym].append(rv / iv)
    by_symbol = {sym: {"moves": len(sym_moves[sym]), "exceeded": sum(a > e for e, a in sym_moves[sym]),
                       "median_move_ratio": _median([a / e for e, a in sym_moves[sym]]), "iv_days": len(sym_ratios[sym]),
                       "median_rv_iv": _median(sym_ratios[sym])} for sym in sorted(set(sym_moves) | set(sym_ratios))}
    # cheap IV picks against every optioned stock on the same day: did they realize more of their IV?
    ratio_on: dict[date, dict[str, float]] = {}
    for sym, d, iv, rv in iv_rows:
        ratio_on.setdefault(d, {})[sym] = rv / iv
    edge: list[float] = []
    for d, picks in cheap_iv:
        day = ratio_on.get(d, {})
        mine = [day[s] for s in picks if s in day]
        rest = [v for s, v in day.items() if s not in set(picks)]
        if mine and len(rest) >= 3:
            edge.append(float(np.mean(mine) - np.mean(rest)))
    cheap = summarize(edge, month, 1)
    cheap["claim"] = "cheap-IV picks realize more of their implied volatility than other optioned stocks"
    return {"iv_vs_realized": {"n": len(iv_rows), "realized_below_implied": _share([rv < iv for *_, iv, rv in iv_rows]),
                               "median_iv_minus_rv": _median([iv - rv for *_, iv, rv in iv_rows]),
                               "median_rv_over_iv": _median([rv / iv for *_, iv, rv in iv_rows])},
            "expected_moves": {"n": len(moves), "exceeded": _share([a > e for *_, e, a in moves]),
                               "median_actual_over_expected": _median([a / e for *_, e, a in moves])},
            "cheap_iv": cheap, "by_symbol": by_symbol}


# ── grades ───────────────────────────────────────────────────────────────────────────────────────
def grade(stat: dict[str, Any] | None, claim: str, source: str) -> dict[str, Any]:
    """A plain verdict on one record at the primary horizon."""
    if stat is None or stat["independent"] < MIN_INDEPENDENT:
        have = 0.0 if stat is None else stat["independent"]
        return {"grade": "early", "source": source, "text": f"Too early: {have:.0f} of {MIN_INDEPENDENT} independent months"}
    if claim not in (*TESTED_CLAIMS, "return", "drawdown", "iv_cheap"):
        return {"grade": "info", "source": source, "text": "Makes no claim; shown for what happened next"}
    p, ph = stat.get("p"), stat.get("p_holm")
    if ph is not None and ph < 0.05:
        return {"grade": "working", "source": source, "text": "Did what it claims, after correcting for every claim tested"}
    if p is not None and p < 0.10:
        return {"grade": "promising", "source": source, "text": "Leaning the right way, not yet beyond chance"}
    if p is not None and p > 0.95:
        return {"grade": "backwards", "source": source, "text": "Did the opposite of what it claims"}
    return {"grade": "not_working", "source": source, "text": "No better than chance so far"}


def _headline(live: dict[str, Any] | None, rebuilt: dict[str, Any] | None, claim: str) -> dict[str, Any]:
    if live is not None and live["independent"] >= MIN_INDEPENDENT:
        return grade(live, claim, "live")
    if rebuilt is not None and rebuilt["independent"] >= MIN_INDEPENDENT:
        g = grade(rebuilt, claim, "rebuilt")
        g["text"] += " (rebuilt from history; the live record is still too short)"
        return g
    return grade(live if live is not None else rebuilt, claim, "live" if live is not None else "rebuilt")


def _apply_holm(stats: list[dict[str, Any]]) -> None:
    tested = [s for s in stats if s.get("p") is not None]
    for s, adjusted in zip(tested, holm([s["p"] for s in tested]), strict=True):
        s["p_holm"] = adjusted


# ── the report ───────────────────────────────────────────────────────────────────────────────────
def current_sectors(market: StocksMarket) -> dict[str, str]:
    companies = market.companies()
    out: dict[str, str] = {}
    for r in research_listings(market):
        c = companies.get(int(r["cik"])) if r["cik"] else None
        out[r["symbol"]] = sector(c["sic"] if c else None)
    return out


def report(market: StocksMarket, research: StocksResearch, panel: Panel, *, today: date) -> dict[str, Any]:
    fw = Forward(panel)
    now_sectors = current_sectors(market)
    history = months(research)
    month_sectors = {m.asof: m.sectors for m in history}

    def sector_of(asof: date, s: str) -> str:
        return month_sectors.get(asof, {}).get(s) or now_sectors.get(s, "Other")

    live_lists = research.snapshots()
    lists: dict[str, Any] = {}
    for spec in LISTS:
        live_obs = live_lists.get(spec.id, [])
        rebuilt_obs = [(m.asof, m.lists[spec.id]) for m in history if spec.id in m.lists]
        live = list_record(fw, live_obs, spec.claim, sector_of, step=1) if live_obs else None
        rebuilt = list_record(fw, rebuilt_obs, spec.claim, sector_of, step=MONTH_SESSIONS) if rebuilt_obs else None
        lists[spec.id] = {"title": spec.title, "claim": spec.claim, "history": spec.history, "tracked": spec.tracked,
                          "badge": spec.badge, "days_recorded": len(live_obs), "months_rebuilt": len(rebuilt_obs),
                          "live": live, "rebuilt": rebuilt,
                          "curve": {"live": growth_curve(fw, month_sample(live_obs), spec.claim),
                                    "rebuilt": growth_curve(fw, rebuilt_obs, spec.claim)}}
    # desks: live scores sampled weekly; rebuilt monthly
    score_days = research.score_days()[::-1][::LIVE_DESK_STEP][::-1]
    live_scores = [(d, research.scores_on(d)) for d in score_days]
    desks: dict[str, Any] = {}
    for d in (*dk.DESKS, "committee"):
        key = "c" if d == "committee" else d
        claim = "return" if d == "committee" else DESK_CLAIMS[d]
        live_obs_d = [(day, {s: v[key] for s, v in rows.items() if v.get(key) is not None}) for day, rows in live_scores]
        rebuilt_obs_d = ([(m.asof, committee_scores(m, dict(dk.DEFAULT_WEIGHTS))) for m in history] if d == "committee"
                         else [(m.asof, {s: v[0] for s, v in m.scores.get(d, {}).items()}) for m in history])
        desks[d] = {"label": dk.DESK_LABELS.get(d, "Committee"), "claim": claim,
                    "live": desk_record(fw, live_obs_d, claim, step=LIVE_DESK_STEP) if live_scores else None,
                    "rebuilt": None if d in LIVE_ONLY_DESKS or not history else
                    desk_record(fw, rebuilt_obs_d, claim, step=MONTH_SESSIONS)}
    options = options_truth(market, fw, live_lists.get("cheap_iv", []))
    for source in ("live", "rebuilt"):
        family = [v[source][PRIMARY] for v in lists.values() if v["claim"] in TESTED_CLAIMS and v[source]]
        family += [v[source] for v in desks.values() if v[source]]
        if source == "live":
            family.append(options["cheap_iv"])
        _apply_holm(family)
    for v in lists.values():
        v["grade"] = _headline((v["live"] or {}).get(PRIMARY), (v["rebuilt"] or {}).get(PRIMARY), v["claim"])
    for v in desks.values():
        v["grade"] = _headline(v["live"], v["rebuilt"], v["claim"])
    options["cheap_iv"]["grade"] = grade(options["cheap_iv"], "iv_cheap", "live")
    learning = learn(fw, history)
    weights = dict(learning["candidate"]) if learning["adopted"] and learning["candidate"] else dict(dk.DEFAULT_WEIGHTS)
    proven = [d for d in LIVE_ONLY_DESKS if desks[d]["grade"]["grade"] == "working"]
    for d in proven:
        weights[d] = PROVEN_LIVE_WEIGHT
    changed = learning["adopted"] or bool(proven)
    return {
        "version": VERSION, "method_sha256": method_sha256(), "rebuild_sha256": rebuild_sha256(),
        "desks_sha256": dk.method_sha256(), "params": content(), "asof": today.isoformat(),
        "prices": {"first": panel.days[0].isoformat() if panel.days else None,
                   "last": panel.days[-1].isoformat() if panel.days else None, "sessions": len(panel.days)},
        "live": {"first": live_scores[0][0].isoformat() if live_scores else None,
                 "days": len(research.score_days()),
                 "lists_first": min((obs[0][0].isoformat() for obs in live_lists.values() if obs), default=None)},
        "rebuild": {"months": len(history), "first": history[0].asof.isoformat() if history else None,
                    "last": history[-1].asof.isoformat() if history else None,
                    "median_universe": int(np.median([m.universe for m in history])) if history else 0,
                    "caveats": REBUILD_CAVEATS},
        "lists": lists, "desks": desks, "options": options, "committee": learning, "proven_live_desks": proven,
        "committee_weights": weights if changed else None,
    }


REBUILD_CAVEATS = [
    "Rebuilt months use today's listings: companies delisted before collection began are missing, which flatters "
    "lists that buy and hurts lists that avoid.",
    "Crowd and options data start on the first day of collection, so those desks and lists have a live record only.",
    "Returns are price only (no dividends) on both sides of every comparison.",
]


def _mean(values: Sequence[float | None]) -> float | None:
    v = [x for x in values if x is not None]
    return None if not v else float(np.mean(v))


def _median(values: Sequence[float]) -> float | None:
    return None if not values else float(np.median(values))


def _share(flags: Sequence[bool]) -> float | None:
    return None if not flags else sum(flags) / len(flags)


GRADE_WORDS = {"working": "WORKING", "promising": "promising", "not_working": "not working", "backwards": "BACKWARDS",
               "early": "too early", "info": "no claim"}


def _pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:+.{digits}f}%"


def _ic(x: float | None) -> str:
    return "—" if x is None else f"{x:+.3f}"


def render(rep: dict[str, Any]) -> str:
    """The report card as plain text (``desk stocks report``)."""
    lines = [f"KESTREL Stocks report card — {rep['asof']} (method {rep['method_sha256'][:12]})",
             f"Prices {rep['prices']['first']} to {rep['prices']['last']} ({rep['prices']['sessions']} sessions); "
             f"live record since {rep['live']['lists_first'] or 'not started'}; "
             f"{rep['rebuild']['months']} rebuilt months ({rep['rebuild']['first'] or '—'} to {rep['rebuild']['last'] or '—'})",
             "", "LISTS (bought at the next open; 1-month return vs SPY; hit rate = picks that did what the list claims)"]
    for v in rep["lists"].values():
        g = v["grade"]
        live = (v["live"] or {}).get(PRIMARY) or {}
        reb = (v["rebuilt"] or {}).get(PRIMARY) or {}
        hit = live.get("hit_rate") if live.get("n") else reb.get("hit_rate")
        lines.append(f"  {v['title'][:34]:34} {GRADE_WORDS[g['grade']]:12} live {_pct(live.get('mean_excess_spy'))} "
                     f"(n={live.get('n', 0)})  rebuilt {_pct(reb.get('mean_excess_spy'))} (n={reb.get('n', 0)})  "
                     f"hit {'—' if hit is None else f'{hit:.0%}'}")
    lines += ["", "DESKS (information coefficient: rank correlation of the score with the next month's return)"]
    for v in rep["desks"].values():
        g = v["grade"]
        live = (v["live"] or {}).get("ic") or {}
        reb = (v["rebuilt"] or {}).get("ic") or {}
        lines.append(f"  {v['label']:20} {GRADE_WORDS[g['grade']]:12} live IC {_ic(live.get('mean'))} (n={live.get('n', 0)})  "
                     f"rebuilt IC {_ic(reb.get('mean'))} (n={reb.get('n', 0)})")
    c = rep["committee"]
    lines += ["", f"COMMITTEE WEIGHTS: {c['why']}"]
    if rep.get("committee_weights"):
        lines.append("  in use: " + ", ".join(f"{k} {v:g}" for k, v in rep["committee_weights"].items() if v))
    o = rep["options"]
    ivr, mv = o["iv_vs_realized"], o["expected_moves"]
    below = "—" if ivr["realized_below_implied"] is None else format(ivr["realized_below_implied"], ".0%")
    exceeded = "—" if mv["exceeded"] is None else format(mv["exceeded"], ".0%")
    lines += ["", "OPTIONS (what was priced vs what happened)",
              f"  Realized volatility below IV30 over the next month: {below} of {ivr['n']}",
              f"  Moves bigger than the first expiry's straddle: {exceeded} of {mv['n']}",
              f"  Cheap-IV list: {GRADE_WORDS[o['cheap_iv']['grade']['grade']]}"]
    lines += ["", "Caveats:"] + [f"  - {x}" for x in rep["rebuild"]["caveats"]]
    lines.append("Research, not financial advice.")
    return "\n".join(lines)


def run(market: StocksMarket, research: StocksResearch, panel: Panel, *, today: date, now: datetime) -> dict[str, Any]:
    """Build the report, freeze the rules it was built under and append it to stk_validation."""
    market.register_spec("stocks_validation", VERSION, content(), now)
    out = report(market, research, panel, today=today)
    out["id"] = research.save_validation(today, out["method_sha256"], out, now)
    return out


__all__ = ["COST_ROUND_TRIP", "HORIZONS", "MIN_INDEPENDENT", "PRIMARY", "REBUILD_CAVEATS", "Forward", "Month",
           "average_ranks", "committee_scores", "content", "desk_record", "fit", "grade", "growth_curve", "learn",
           "list_record", "method_sha256", "month_sample", "months", "one_sided_p", "options_truth", "rebuild",
           "rebuild_days", "rebuild_sha256", "render", "report", "run", "spearman", "summarize"]
