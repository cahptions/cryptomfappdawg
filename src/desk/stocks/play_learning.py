"""Plays learning: refine the picks from how they did, without fooling itself (docs/STOCKS_PLAYS.md → Learning).

* **Calibration** of KESTREL's chance per category and days to expiry (``plays.calibration``), falling back to the
  category and then to every play until a group has 30 resolved plays.
* **Exit rules**: take profit at +X%, stop at −Y% or leave after N sessions, tried on each category's quoted closing
  marks. The rule is chosen walk-forward: at each block of five sessions, the best rule on plays that had fully
  resolved before the block is applied to the block's plays, which it never saw, and compared with holding to expiry.
* **Which inputs predict a play's return**: each session's rank correlation (information coefficient) between an input
  at the pick and the return a week later, averaged over sessions with a stationary block bootstrap.
* **Challengers**: preregistered changes to the rules run in shadow beside the live ones; their picks are recorded
  under their own version hash and graded at expiry. One is promoted only when it beats the live rules on later
  sessions — a stationary block bootstrap over the session series of differences (nearby sessions are not
  independent), Holm-corrected across every challenger tested. Failed challengers stay listed with their results.
* **Labels**: a category with enough settled plays that significantly beats its random twins is *proven*; one that
  significantly trails them (or loses money) is *failing*. Both are shown; nothing is hidden.

Only quoted marks and exact settlements are used. Every record keeps the rule version it was picked under, and no
grade mixes versions.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from itertools import product
from typing import Any

import numpy as np

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stats.core import holm
from desk.stocks import calendar as cal
from desk.stocks import validation as val
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.play_marks import settle_day, settlement
from desk.stocks.play_performance import PlayReturn, returns_table
from desk.stocks.repo import day_of
from desk.storage.db import transaction

VERSION = 1
PER_CATEGORY = 12
SCAN = 60                          # candidates finished per category when a challenger re-ranks or filters
MIN_SESSIONS = 20                  # sessions with settled plays before a label or a promotion
MIN_RESOLVED = 60
ALPHA = 0.05
FAIL_AFTER = 40                    # sessions without a significant win: the challenger has failed
MIN_TRAIN_PLAYS = 30
TEST_BLOCK = 5
TAKE_PROFIT = (0.25, 0.5, 1.0, 2.0, None)
STOP_LOSS = (0.25, 0.5, 0.75, None)
TIME_EXIT = (5, 10, 21, None)
IC_HORIZON = 5                     # sessions: the return a week after the pick


# ── challengers ──────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Variant:
    id: str
    title: str
    rule: str                                                   # what it changes, in words (part of its hash)
    keep: Callable[[dict[str, Any], str], bool] | None = None
    rank: Callable[[dict[str, Any]], float] | None = None
    categories: tuple[str, ...] | None = None                   # None: every category

    @property
    def hash(self) -> str:
        return sha256_hex(canonical_json({"version": VERSION, "id": self.id, "rule": self.rule}))[:16]

    def applies(self, category: str) -> bool:
        return self.categories is None or category in self.categories


VARIANTS: tuple[Variant, ...] = (
    Variant("liquid_only", "Liquid contracts only",
            "keep only plays graded A or B for liquidity (widest spread at most 12%, open interest at least 250)",
            keep=lambda p, _: p.get("liquidity") in ("A", "B")),
    Variant("tight_spread", "Spreads under 6%", "keep only plays whose widest leg is quoted at most 6% wide",
            keep=lambda p, _: float(p.get("spread_pct") or 1.0) <= 0.06),
    Variant("positive_ev", "Positive expected value only",
            "keep only plays whose expected value after fees under KESTREL's forecast is above zero",
            keep=lambda p, _: (p.get("expected_value") or -1.0) > 0),
    Variant("edge_rank", "Ranked by KESTREL's edge over the market",
            "rank each category by KESTREL's chance minus the market's chance instead of the category score",
            rank=lambda p: float(p.get("itm_kestrel") or 0) - float(p.get("itm_market") or 0)),
    Variant("no_results", "No results before expiry",
            "skip plays with a results window before expiry (Earnings plays excepted)",
            keep=lambda p, _: not p.get("earnings"),
            categories=("likely_itm", "edge", "unusual", "cheap_vol", "trend_calls", "bear_puts", "squeeze",
                        "sell_premium", "lottos", "leaps", "watchlist")),
)
BY_VARIANT = {v.id: v for v in VARIANTS}


def select(variant: Variant, category: str, found: Sequence[Any], finish: Callable[[Any], dict[str, Any]],
           per_symbol: int) -> list[dict[str, Any]]:
    """What ``variant`` would pick in ``category`` from the same sorted candidates the live rules saw."""
    pool: list[dict[str, Any]] = []
    seen: Counter[str] = Counter()
    for scanned, c in enumerate(found):
        if scanned >= SCAN or (variant.rank is None and len(pool) >= PER_CATEGORY):
            break
        symbol = c.table.u.symbol
        if seen[symbol] >= per_symbol:
            continue
        p = finish(c)
        if variant.keep is not None and not variant.keep(p, category):
            continue
        seen[symbol] += 1
        pool.append(p)
    if variant.rank is not None:
        rank = variant.rank
        pool.sort(key=lambda p: -rank(p))
    return [{**p, "rank": i + 1} for i, p in enumerate(pool[:PER_CATEGORY])]


def record_shadow(research: sqlite3.Connection, session: date, picks: dict[str, dict[str, list[dict[str, Any]]]],
                  now: datetime) -> int:
    rows: list[tuple[Any, ...]] = []
    days: list[tuple[Any, ...]] = []
    for variant_id, by_category in picks.items():
        v = BY_VARIANT[variant_id]
        for category, plays in by_category.items():
            days.append((day_of(session), variant_id, category, len(plays), to_iso(now)))
            rows.extend((day_of(session), variant_id, category, p["rank"], p["symbol"], p["action"], p["structure"],
                         day_of(date.fromisoformat(p["expiration"])), canonical_json(p["legs"]), p["entry"],
                         p["max_loss"], v.hash, canonical_json(p), to_iso(now)) for p in plays)
    with transaction(research):
        fresh = {(r[1], r[2]) for r in days if research.execute(
            "SELECT 1 FROM stk_play_shadow_days WHERE session = ? AND variant = ? AND category = ?",
            r[:3]).fetchone() is None}
        research.executemany("INSERT OR IGNORE INTO stk_play_shadow_days VALUES (?, ?, ?, ?, ?)", days)
        research.executemany("INSERT OR IGNORE INTO stk_play_shadow VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             [r for r in rows if (r[1], r[2]) in fresh])
    return len(rows)


def grade_shadow(market: sqlite3.Connection, research: sqlite3.Connection, now: datetime) -> int:
    """Grade challenger picks once at expiration on the stock's close, exactly as the live picks are graded."""
    latest = market.execute("SELECT MAX(day) FROM stk_bar_days WHERE rows > 0").fetchone()[0]
    if latest is None:
        return 0
    pending = research.execute(
        "SELECT s.* FROM stk_play_shadow s LEFT JOIN stk_play_shadow_outcomes o USING (session, variant, category, "
        "rank) WHERE o.session IS NULL AND s.expiration <= ?", (latest,)).fetchall()
    rows = []
    for p in pending:
        expiry = settle_day(date.fromordinal(p["expiration"]))
        bar = market.execute("SELECT close FROM stk_bars WHERE symbol = ? AND day = ?",
                             (p["symbol"], day_of(expiry))).fetchone()
        split = market.execute("SELECT 1 FROM stk_splits WHERE symbol = ? AND execution_date > ? AND execution_date <= ?",
                               (p["symbol"], p["session"], p["expiration"])).fetchone()
        if bar is None or split is not None:
            continue
        legs = json.loads(p["legs_json"])
        value = settlement(legs, float(bar["close"]))
        fees = FEE * len(legs) / 100
        net = ((value - p["entry"] - fees) / (p["entry"] + fees) if p["action"] == "BUY"
               else (p["entry"] + value - fees) / (p["max_loss"] / 100))
        rows.append((p["session"], p["variant"], p["category"], p["rank"], to_iso(now), float(bar["close"]), value, net))
    with transaction(research):
        research.executemany("INSERT OR IGNORE INTO stk_play_shadow_outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def promoted(research: sqlite3.Connection) -> dict[str, str]:
    """The challenger each category runs live now (its latest promotion), if any."""
    out: dict[str, str] = {}
    for r in research.execute("SELECT category, variant FROM stk_play_promotions ORDER BY id"):
        if r["variant"] in BY_VARIANT:
            out[r["category"]] = r["variant"]
    return out


def _series(diffs: dict[int, list[float]]) -> np.ndarray:
    return np.array([statistics.fmean(v) for _, v in sorted(diffs.items())], dtype=float)


def challengers(research: sqlite3.Connection, categories: Iterable[str]) -> list[dict[str, Any]]:
    """Each challenger against the live picks of the same sessions and category, graded at expiry."""
    base: dict[tuple[int, str], list[float]] = defaultdict(list)
    for r in research.execute("SELECT session, category, net_return FROM stk_play_outcomes"):
        base[(r[0], r[1])].append(float(r[2]))
    shadow: dict[tuple[str, str], dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in research.execute("SELECT session, variant, category, net_return FROM stk_play_shadow_outcomes"):
        shadow[(r["variant"], r["category"])][r["session"]].append(float(r["net_return"]))
    live = promoted(research)
    tests: list[dict[str, Any]] = []
    for v in VARIANTS:
        for category in categories:
            if not v.applies(category):
                continue
            mine = shadow.get((v.id, category), {})
            diffs = {s: [statistics.fmean(rets) - statistics.fmean(base[(s, category)])]
                     for s, rets in mine.items() if base.get((s, category))}
            x = _series(diffs)
            tests.append({"variant": v.id, "title": v.title, "rule": v.rule, "hash": v.hash, "category": category,
                          "sessions": len(x), "mean_difference": round(float(x.mean()), 5) if len(x) else None,
                          "p": val.one_sided_p(x) if len(x) >= 3 else None, "live": live.get(category) == v.id})
    eligible = [t for t in tests if t["p"] is not None and not t["live"]]
    for t, adjusted in zip(eligible, holm([t["p"] for t in eligible]), strict=True):
        t["p_holm"] = round(adjusted, 4)
    for t in tests:
        t.setdefault("p_holm", None)
        if t["live"]:
            t["status"] = "promoted"
        elif (t["p_holm"] is not None and t["p_holm"] < ALPHA and t["sessions"] >= MIN_SESSIONS
              and (t["mean_difference"] or 0) > 0):
            t["status"] = "beats the live rules"
        elif t["sessions"] >= FAIL_AFTER:
            t["status"] = "failed"
        else:
            t["status"] = f"testing ({t['sessions']} of {MIN_SESSIONS} sessions)"
    return tests


def promote(research: sqlite3.Connection, tests: Sequence[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Promote, per category, the challenger with the largest significant improvement on later sessions."""
    done = []
    winners: dict[str, dict[str, Any]] = {}
    for t in tests:
        if t["status"] == "beats the live rules" and (
                t["category"] not in winners or t["mean_difference"] > winners[t["category"]]["mean_difference"]):
            winners[t["category"]] = t
    with transaction(research):
        for category, t in sorted(winners.items()):
            v = BY_VARIANT[t["variant"]]
            research.execute("INSERT INTO stk_play_promotions(created_at, category, variant, params_json, evidence_json)"
                             " VALUES (?, ?, ?, ?, ?)", (to_iso(now), category, v.id,
                                                          canonical_json({"rule": v.rule, "hash": v.hash}),
                                                          canonical_json(t)))
            done.append({"category": category, "variant": v.id})
    return done


# ── exit rules, walk-forward ─────────────────────────────────────────────────────────────────────
Rule = tuple[float | None, float | None, int | None]


def rules() -> list[Rule]:
    """Every candidate exit rule; holding to expiry first, so it wins ties."""
    hold: Rule = (None, None, None)
    return [hold, *[r for r in product(TAKE_PROFIT, STOP_LOSS, TIME_EXIT) if r != hold]]


def describe(rule: Sequence[float | int | None]) -> str:
    tp, sl, t = rule
    parts = ([f"take profit at +{tp:.0%}"] if tp else []) + ([f"stop at −{sl:.0%}"] if sl else []) + (
        [f"leave after {t} sessions"] if t else [])
    return ", ".join(parts) or "hold to expiry"


def exit_return(path: Sequence[tuple[int, float, str]], rule: Rule) -> tuple[float, int] | None:
    """(return, sessions held) under ``rule``, exiting at a quoted close; None while the outcome is still unknown."""
    tp, sl, t = rule
    for i, r, s in sorted(path):
        if s == "settled":
            return r, i
        if s != "quoted" or i == 0:
            continue                                # exits act on later quoted closes, never on estimates
        if (tp is not None and r >= tp) or (sl is not None and r <= -sl) or (t is not None and i >= t):
            return r, i
    return None


def walk_forward(rows: Sequence[PlayReturn]) -> dict[str, Any]:
    """Choose a rule on plays fully resolved before each test block; apply it to the block; compare with holding."""
    candidates = rules()
    outcome = np.full((len(candidates), len(rows)), np.nan)
    for j, r in enumerate(rows):
        for i, rule in enumerate(candidates):
            got = exit_return(r.path, rule)
            if got is not None:
                outcome[i, j] = got[0]
    sessions = sorted({r.session for r in rows})
    picked = np.array([r.session.toordinal() for r in rows])
    resolved = np.array([settle_day(r.expiration).toordinal() for r in rows])

    def best(mask: np.ndarray) -> int:
        counts = np.sum(~np.isnan(outcome[:, mask]), axis=1)
        means = np.where(counts > 0, np.nansum(outcome[:, mask], axis=1) / np.maximum(counts, 1), -np.inf)
        return int(np.argmax(means))                # the first maximum: holding to expiry wins ties

    diffs: dict[int, list[float]] = defaultdict(list)
    chosen: list[str] = []
    for k in range(MIN_SESSIONS, len(sessions), TEST_BLOCK):
        start = sessions[k].toordinal()
        end = sessions[k + TEST_BLOCK].toordinal() if k + TEST_BLOCK < len(sessions) else 10**9
        train = (picked < start) & (resolved < start) & ~np.isnan(outcome[0])
        if train.sum() < MIN_TRAIN_PLAYS:
            continue
        b = best(train)
        chosen.append(describe(candidates[b]))
        for j in np.where((picked >= start) & (picked < end))[0]:
            if not np.isnan(outcome[b, j]) and not np.isnan(outcome[0, j]):
                diffs[int(picked[j])].append(float(outcome[b, j] - outcome[0, j]))
    x = _series(diffs)
    full = ~np.isnan(outcome[0])
    overall = None
    if full.sum() >= MIN_TRAIN_PLAYS:
        b = best(full)
        overall = {"rule": describe(candidates[b]), "params": list(candidates[b]),
                   "mean": round(float(np.nanmean(outcome[b, full])), 5),
                   "hold_mean": round(float(np.nanmean(outcome[0, full])), 5)}
    return {"test_sessions": len(x), "mean_improvement": round(float(x.mean()), 5) if len(x) else None,
            "p": val.one_sided_p(x) if len(x) >= 3 else None, "chosen": Counter(chosen).most_common(3),
            "best_in_sample": overall}


# ── which inputs predict a play's return ─────────────────────────────────────────────────────────
FEATURES: tuple[tuple[str, str, Callable[[dict[str, Any]], float | None]], ...] = (
    ("itm_kestrel", "KESTREL's chance", lambda p: p.get("itm_kestrel")),
    ("edge", "KESTREL minus market chance", lambda p: None if p.get("itm_kestrel") is None or p.get("itm_market")
     is None else float(p["itm_kestrel"]) - float(p["itm_market"])),
    ("ev_ratio", "Expected value per $ at risk", lambda p: None if not p.get("max_loss") or p.get("expected_value")
     is None else float(p["expected_value"]) / float(p["max_loss"])),
    ("iv", "Implied volatility", lambda p: p.get("iv")),
    ("iv_rank", "IV rank", lambda p: p.get("iv_rank")),
    ("spread_pct", "Bid/ask spread", lambda p: p.get("spread_pct")),
    ("vol_oi", "Volume ÷ open interest", lambda p: p.get("vol_oi")),
    ("abs_delta", "|Delta|", lambda p: None if p.get("delta") is None else abs(float(p["delta"]))),
    ("dte", "Days to expiry", lambda p: p.get("dte")),
    ("score", "Category score", lambda p: p.get("score")),
)


def feature_ics(rows: Sequence[PlayReturn]) -> list[dict[str, Any]]:
    by_session: dict[date, list[PlayReturn]] = defaultdict(list)
    for r in rows:
        if r.at(IC_HORIZON) is not None:
            by_session[r.session].append(r)
    out = []
    for key, label, get in FEATURES:
        ics = []
        for _, group in sorted(by_session.items()):
            xs = np.array([np.nan if get(r.play) is None else float(get(r.play) or 0) for r in group], dtype=float)
            ys = np.array([float(r.at(IC_HORIZON) or 0) for r in group], dtype=float)
            ic = val.spearman(xs, ys)
            if ic is not None:
                ics.append(ic)
        summary = val.summarize(ics, 1, 1)
        out.append({"feature": key, "label": label, "sessions": len(ics),
                    "ic": None if summary["mean"] is None else round(summary["mean"], 4),
                    "ci": summary["ci"], "p": summary["p"]})
    return out


# ── labels ───────────────────────────────────────────────────────────────────────────────────────
def labels(rows: Sequence[PlayReturn], categories: Iterable[str], paper: dict[str, str | None]) -> dict[str, Any]:
    tests: list[dict[str, Any]] = []
    for category in categories:
        mine = [r for r in rows if r.category == category and r.at(None) is not None]
        by_session: dict[int, list[float]] = defaultdict(list)
        own: dict[int, list[float]] = defaultdict(list)
        for r in mine:
            own[day_of(r.session)].append(float(r.at(None) or 0))
            twin = r.twin_at(None)
            if twin is not None:
                by_session[day_of(r.session)].append(float(r.at(None) or 0) - twin)
        x, y = _series(by_session), _series(own)
        tests.append({"category": category, "settled": len(mine), "sessions": len(y),
                      "mean": round(float(y.mean()), 5) if len(y) else None,
                      "vs_twins": round(float(x.mean()), 5) if len(x) else None,
                      "p_better": val.one_sided_p(x) if len(x) >= 3 else None,
                      "p_worse": val.one_sided_p(-x) if len(x) >= 3 else None,
                      "p_losing": val.one_sided_p(-y) if len(y) >= 3 else None})
    for key in ("p_better", "p_worse", "p_losing"):
        ready = [t for t in tests if t[key] is not None]
        for t, adjusted in zip(ready, holm([t[key] for t in ready]), strict=True):
            t[key + "_holm"] = round(adjusted, 4)
    out: dict[str, Any] = {}
    for t in tests:
        enough = t["settled"] >= MIN_RESOLVED and t["sessions"] >= MIN_SESSIONS
        better, worse, losing = (t.get(k + "_holm") for k in ("p_better", "p_worse", "p_losing"))
        if enough and better is not None and better < ALPHA and (t["mean"] or 0) > 0:
            state = "proven"
            text = (f"Proven: beats random twins by {t['vs_twins']:+.1%} a play over {t['sessions']} sessions "
                    f"(Holm p {better:.3f})")
            text += (f"; traded in PAPER as {paper[t['category']]}" if paper.get(t["category"])
                     else "; eligible to become a PAPER strategy")
        elif enough and ((worse is not None and worse < ALPHA) or (losing is not None and losing < ALPHA)):
            state = "failing"
            text = (f"Failing: {t['mean']:+.1%} a play at expiry, {t['vs_twins']:+.1%} against random twins over "
                    f"{t['sessions']} sessions")
        else:
            state = "unproven"
            text = (f"Not enough evidence yet: {t['settled']} of {MIN_RESOLVED} settled plays over {t['sessions']} of "
                    f"{MIN_SESSIONS} sessions" if not enough else "No significant difference from random twins yet")
        out[t["category"]] = {"state": state, "text": text, **t}
    return out


# ── one report per closed session ────────────────────────────────────────────────────────────────
def learn(market: sqlite3.Connection, research: sqlite3.Connection, now: datetime) -> dict[str, Any] | None:
    """Grade challengers, then (once per closed session) write the learning report and any promotion."""
    from desk.stocks import plays as pl

    graded = grade_shadow(market, research, now)
    session = cal.last_closed_session(now)
    live = promoted(research)
    m = pl.method(live)
    if research.execute("SELECT 1 FROM stk_play_learning WHERE session = ? AND method = ?",
                        (day_of(session), m)).fetchone():
        return None
    rows = returns_table(research)
    categories = [c.id for c in pl.CATEGORIES]
    current = [r for r in rows if r.method == m] or rows
    tests = challengers(research, categories)
    exits: dict[str, dict[str, Any]] = {c: walk_forward([r for r in current if r.category == c]) for c in categories
                                        if any(r.category == c for r in current)}
    report: dict[str, Any] = {"version": VERSION, "session": session.isoformat(), "method": m,
              "generated_at": now.isoformat(),
              "graded_challenger_picks": graded, "promoted": live,
              "calibration": pl.calibration_summary(research),
              "exits": exits,
              "features": feature_ics(current),
              "challengers": tests,
              "labels": labels(current, categories, {c.id: c.paper for c in pl.CATEGORIES})}
    ready = [e for e in exits.values() if e["p"] is not None]
    for e, adjusted in zip(ready, holm([e["p"] for e in ready]), strict=True):
        e["p_holm"] = round(adjusted, 4)
    for e in exits.values():
        e.setdefault("p_holm", None)
        e["suggested"] = bool(e["p_holm"] is not None and e["p_holm"] < ALPHA and e["test_sessions"] >= MIN_SESSIONS
                              and (e["mean_improvement"] or 0) > 0 and e["best_in_sample"])
    report["promotions"] = promote(research, tests, now)
    with transaction(research):
        research.execute("INSERT INTO stk_play_learning(created_at, session, method, report_json) VALUES (?, ?, ?, ?)",
                         (to_iso(now), day_of(session), m, json.dumps(report, allow_nan=False, default=str)))
    return report


def latest(research: sqlite3.Connection) -> dict[str, Any] | None:
    row = research.execute("SELECT report_json FROM stk_play_learning ORDER BY id DESC LIMIT 1").fetchone()
    return None if row is None else dict(json.loads(row[0]))


__all__ = ["BY_VARIANT", "VARIANTS", "Variant", "challengers", "describe", "exit_return", "feature_ics",
           "grade_shadow", "labels", "latest", "learn", "promote", "promoted", "record_shadow", "rules", "select",
           "walk_forward"]
