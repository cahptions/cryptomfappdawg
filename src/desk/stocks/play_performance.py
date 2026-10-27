"""Plays performance across every recorded session (docs/STOCKS_PLAYS.md → Performance). Built in the background after
the close and saved as one JSON view, so the page does no computation.

Only quoted marks and exact settlements count: a return at a holding period is the play's quoted closing mark that
many sessions after the pick (or its settlement, if it expired sooner); estimated and modelled marks are left out.
Each rule version is graded on its own. The random twins (same structure, expiry and delta on another stock) and SPY
held over the same dates are the baselines.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from desk.core.paths import DeskPaths
from desk.stocks import calendar as cal
from desk.stocks.plays import CATEGORIES, method

VERSION = 1
HORIZONS: tuple[tuple[str, int | None], ...] = (("1 day", 1), ("1 week", 5), ("1 month", 21), ("At expiry", None))
DTE_BUCKETS = ((0, 7, "≤ 7 days"), (8, 30, "8–30 days"), (31, 90, "31–90 days"), (91, 365, "91–365 days"),
               (366, 100_000, "over a year"))
PRICE_BUCKETS = ((0.0, 0.5, "under $0.50"), (0.5, 2.0, "$0.50–$2"), (2.0, 5.0, "$2–$5"), (5.0, 20.0, "$5–$20"),
                 (20.0, 1e12, "$20 and up"))
GRADED = frozenset({"quoted", "settled"})


@dataclass
class PlayReturn:
    """One recorded play's graded path: (sessions after the pick, return on the money at risk, status)."""
    session: date
    category: str
    rank: int
    method: str
    symbol: str
    action: str
    structure: str
    expiration: date
    entry: float
    dte: int
    play: dict[str, Any]
    path: list[tuple[int, float, str]] = field(default_factory=list)
    twin: list[tuple[int, float, str]] = field(default_factory=list)
    hit: bool | None = None

    @staticmethod
    def _at(path: Sequence[tuple[int, float, str]], horizon: int | None) -> float | None:
        settled = next(((i, r) for i, r, s in path if s == "settled"), None)
        if horizon is None:
            return None if settled is None else settled[1]
        if settled is not None and settled[0] <= horizon:
            return settled[1]
        return next((r for i, r, s in path if i == horizon and s in GRADED), None)

    def at(self, horizon: int | None) -> float | None:
        return self._at(self.path, horizon)

    def twin_at(self, horizon: int | None) -> float | None:
        return self._at(self.twin, horizon)

    @property
    def settled_index(self) -> int | None:
        return next((i for i, _, s in self.path if s == "settled"), None)


def returns_table(research: sqlite3.Connection) -> list[PlayReturn]:
    rows = research.execute("SELECT p.*, o.hit FROM stk_plays p LEFT JOIN stk_play_outcomes o USING (session, category,"
                            " rank) ORDER BY p.session, p.category, p.rank").fetchall()
    if not rows:
        return []
    first = date.fromordinal(rows[0]["session"])
    newest = research.execute("SELECT MAX(day) FROM stk_play_marks").fetchone()[0]
    last = date.fromordinal(newest) if newest else first
    index = {d.toordinal(): i for i, d in enumerate(cal.trading_days(first, max(first, last)))}
    out: dict[tuple[int, str, int], PlayReturn] = {}
    for r in rows:
        session = date.fromordinal(r["session"])
        out[(r["session"], r["category"], r["rank"])] = PlayReturn(
            session, r["category"], r["rank"], r["method"], r["symbol"], r["action"], r["structure"],
            date.fromordinal(r["expiration"]), r["entry"], (date.fromordinal(r["expiration"]) - session).days,
            json.loads(r["play_json"]), hit=None if r["hit"] is None else bool(r["hit"]))
    for m in research.execute("SELECT session, category, rank, twin, day, status, pnl_pct FROM stk_play_marks "
                              "ORDER BY day"):
        p = out.get((m["session"], m["category"], m["rank"]))
        if p is None or m["day"] not in index or m["session"] not in index:
            continue
        step = (index[m["day"]] - index[m["session"]], float(m["pnl_pct"]), m["status"])
        (p.twin if m["twin"] else p.path).append(step)
    return list(out.values())


def stats(values: Sequence[float | None]) -> dict[str, Any] | None:
    xs = [v for v in values if v is not None]
    if not xs:
        return None
    return {"n": len(xs), "mean": round(statistics.fmean(xs), 5), "median": round(statistics.median(xs), 5),
            "hit_rate": round(sum(x > 0 for x in xs) / len(xs), 4)}


def by_horizon(rows: Sequence[PlayReturn]) -> list[dict[str, Any]]:
    out = []
    for label, h in HORIZONS:
        paired = [(r.at(h), r.twin_at(h)) for r in rows]
        out.append({"label": label, "sessions": h, "plays": stats([a for a, _ in paired]),
                    "twins": stats([b for a, b in paired if a is not None])})
    return out


def _bucket(value: float, buckets: Sequence[tuple[float, float, str]]) -> str:
    return next(label for lo, hi, label in buckets if lo <= value <= hi)


def calibration(rows: Sequence[PlayReturn]) -> dict[str, Any]:
    """Predicted chance of the play's event vs how often it happened, in tenths."""
    out: dict[str, Any] = {}
    for key, label in (("itm_kestrel", "KESTREL"), ("itm_market", "Market")):
        bins: dict[int, list[tuple[float, bool]]] = defaultdict(list)
        for r in rows:
            p = r.play.get(key)
            if p is None or r.hit is None:
                continue
            bins[min(int(float(p) * 10), 9)].append((float(p), r.hit))
        points = [{"predicted": round(statistics.fmean(p for p, _ in v), 4),
                   "observed": round(sum(h for _, h in v) / len(v), 4), "n": len(v),
                   "bucket": f"{10 * b}–{10 * b + 10}%"} for b, v in sorted(bins.items())]
        resolved = [(p, h) for v in bins.values() for p, h in v]
        out[key] = {"label": label, "points": points, "resolved": len(resolved),
                    "brier": round(statistics.fmean((p - h) ** 2 for p, h in resolved), 4) if resolved else None}
    return out


def spy_closes(market: sqlite3.Connection) -> dict[int, float]:
    return {r["day"]: float(r["close"]) for r in market.execute("SELECT day, close FROM stk_bars WHERE symbol = 'SPY'")}


def cumulative(rows: Sequence[PlayReturn], spy: dict[int, float]) -> list[dict[str, Any]]:
    """$100 into every play when picked, counted when it settles; the same into its twin and into SPY."""
    if not rows:
        return []
    days = cal.trading_days(min(r.session for r in rows), max(r.expiration for r in rows))
    position = {d: i for i, d in enumerate(days)}
    by_day: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0])
    for r in rows:
        ret, i = r.at(None), r.settled_index
        if ret is None or i is None or r.session not in position or position[r.session] + i >= len(days):
            continue
        day = days[position[r.session] + i]
        acc = by_day[day]
        acc[0] += 100 * ret
        acc[3] += 1
        twin = r.twin_at(None)
        if twin is not None:
            acc[1] += 100 * twin
        s0, s1 = spy.get(r.session.toordinal()), spy.get(day.toordinal())
        if s0 and s1:
            acc[2] += 100 * (s1 / s0 - 1)
            acc[4] += 1
    out, total = [], [0.0, 0.0, 0.0, 0.0, 0.0]
    for day in sorted(by_day):
        total = [a + b for a, b in zip(total, by_day[day], strict=True)]
        out.append({"t": day.isoformat(), "picks": round(total[0], 2), "twins": round(total[1], 2),
                    "spy": round(total[2], 2), "settled": int(total[3]), "invested": round(100 * total[3], 2)})
    return out


def build(market: sqlite3.Connection, research: sqlite3.Connection, now: datetime,
          labels: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = returns_table(research)
    current = method()
    spy = spy_closes(market)
    categories = []
    for c in CATEGORIES:
        mine = [r for r in rows if r.category == c.id]
        if not mine:
            continue
        versions = sorted({r.method for r in mine}, key=lambda m: (m != current, -max(
            r.session.toordinal() for r in mine if r.method == m)))
        categories.append({
            "id": c.id, "title": c.title, "emoji": c.emoji, "label": (labels or {}).get(c.id),
            "versions": [{"method": m, "current": m == current, "plays": sum(r.method == m for r in mine),
                          "open": sum(r.method == m and r.at(None) is None for r in mine),
                          "horizons": by_horizon([r for r in mine if r.method == m])} for m in versions]})
    graded = [r for r in rows if r.method == current] or rows

    def buckets(key: str, edges: Sequence[tuple[float, float, str]]) -> list[dict[str, Any]]:
        groups: dict[str, list[PlayReturn]] = defaultdict(list)
        for r in graded:
            groups[_bucket(float(getattr(r, key)), edges)].append(r)
        return [{"label": label, "plays": len(groups[label]), "week": stats([r.at(5) for r in groups[label]]),
                 "expiry": stats([r.at(None) for r in groups[label]]),
                 "twins_expiry": stats([r.twin_at(None) for r in groups[label] if r.at(None) is not None])}
                for _, _, label in edges if groups.get(label)]

    curves = {"all": cumulative(graded, spy)}
    for c in CATEGORIES:
        mine = [r for r in graded if r.category == c.id]
        if mine:
            curves[c.id] = cumulative(mine, spy)
    return {"version": VERSION, "generated_at": now.isoformat(), "method": current,
            "plays": len(rows), "graded_version_plays": len(graded),
            "resolved": sum(r.at(None) is not None for r in rows),
            "sessions": len({r.session for r in rows}),
            "categories": categories, "overall": by_horizon(graded),
            "dte": buckets("dte", DTE_BUCKETS), "price": buckets("entry", PRICE_BUCKETS),
            "calibration": calibration(rows), "cumulative": curves,
            "note": "Returns are on the money at risk after fees, from quoted closing marks and exact settlements only. "
                    "Holding periods count sessions after the pick; a play that expired sooner counts its settlement."}


REBUILD_AFTER = timedelta(hours=12)


def refresh(paths: DeskPaths, market: sqlite3.Connection, research: sqlite3.Connection, now: datetime, *,
            changed: bool) -> bool:
    """Rebuild the saved view when new marks arrived, or at least twice a day."""
    from desk.stocks.connection import _atomic_private
    from desk.stocks.runner import saved, saved_path

    try:
        previous = saved(paths, "plays-performance")
    except Exception:  # unreadable: rebuilt below
        previous = None
    stale = previous is None or now - datetime.fromisoformat(previous["generated_at"]) >= REBUILD_AFTER
    if not changed and not stale:
        return False
    learned = research.execute("SELECT report_json FROM stk_play_learning ORDER BY id DESC LIMIT 1").fetchone()
    labels = json.loads(learned[0]).get("labels") if learned else None
    _atomic_private(saved_path(paths, "plays-performance"),
                    json.dumps(build(market, research, now, labels), allow_nan=False))
    return True


__all__ = ["DTE_BUCKETS", "HORIZONS", "PRICE_BUCKETS", "PlayReturn", "build", "by_horizon", "calibration",
           "cumulative", "refresh", "returns_table", "stats"]
