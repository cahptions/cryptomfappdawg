"""Where each trade's money came from (docs/STOCKS_LEARNING.md → Where the money came from).

For every closed PAPER / Learner option trade and every graded play with stored daily marks, the profit or loss is
split into:

* **direction**: Σ legs × (delta × the stock's move + ½ gamma × the move²), step by step;
* **volatility**: Σ legs × vega × the change in implied volatility;
* **time decay**: Σ legs × theta × calendar days;
* **costs**: the half spreads paid on the way in and out (fill against the mid) and the fees;
* **unexplained**: whatever is left, shown as it is (larger moves than the greeks describe, a missing day, a
  greek that changed within a step).

Each step runs between two observations where every leg had a **stored** quote: the entry session's chain, each
stored session's chain (PAPER) or each quoted closing mark (plays), and the exit fill or the settlement at intrinsic
value on the official close. Greeks are Black-Scholes at the start of the step from the implied volatility solved
from that stored mid. Nothing modelled is used: a trade closed at a modelled price, or after a split, is left out; a
leg without an implied volatility puts its step in "unexplained". The parts always add up to the trade's recorded
profit or loss.

``run`` attributes what has closed since the last pass (bounded) into ``stk_pl_attribution`` (append-only);
``summary`` aggregates by strategy or section; ``of_trade`` reads one trade's split.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import pairwise
from statistics import fmean
from typing import Any

from desk.core.clock import to_iso
from desk.stocks import options as opt
from desk.stocks.paper.fills import CONTRACT

VERSION = 1
PARTS = ("direction", "volatility", "time", "costs", "unexplained")
WORDS = {"direction": "direction moves", "volatility": "volatility changes", "time": "time decay",
         "costs": "costs (spreads and fees)", "unexplained": "unexplained"}
MAX_POINTS = 40                    # observations a trade is split over at most (evenly spaced, ends kept)
PER_PASS = 200                     # trades attributed per call
CONSISTENT = 0.6                   # a source is consistent when at least this share of trades agree in sign
MIN_TRADES = 10                    # trades a group needs before its consistent sources are named
DEFAULT_RATE = 0.04


@dataclass(frozen=True)
class Leg:
    occ: str
    kind: str
    strike: float
    expiration: date
    qty: float                      # signed contracts (bought > 0), units included


@dataclass
class Point:
    day: date
    s: float
    mids: dict[str, float]
    ivs: dict[str, float | None] = field(default_factory=dict)


def _iv(leg: Leg, mid: float, s: float, day: date, rate: float) -> float | None:
    if mid <= 0 or s <= 0:
        return None
    return opt.implied_vol(leg.kind, mid, s, leg.strike, opt.year_fraction(day, leg.expiration), rate)


def _intrinsic(leg: Leg, s: float) -> float:
    return max(0.0, s - leg.strike) if leg.kind == "call" else max(0.0, leg.strike - s)


def split(legs: Sequence[Leg], points: Sequence[Point], entry: Mapping[str, float], total: float,
          exit_prices: Mapping[str, float] | None, settle: float | None, rate: float) -> dict[str, Any]:
    """The trade's P/L in parts. ``points`` are the observations (entry first), ``entry`` each leg's fill price,
    ``exit_prices`` each leg's exit fill (None when it settled at ``settle``, the underlying's official close).
    ``total`` is the recorded profit or loss after fees; the parts always add up to it."""
    path = [p for p in points if all(leg.occ in p.mids for leg in legs)]
    if len(path) > MAX_POINTS:
        keep = sorted({round(i * (len(path) - 1) / (MAX_POINTS - 1)) for i in range(MAX_POINTS)})
        path = [path[i] for i in keep]
    notes: list[str] = []
    if not path:
        notes.append("no stored quote of every leg on the way: the move is unexplained")
    end = None if settle is None else Point(date.max, settle, {leg.occ: _intrinsic(leg, settle) for leg in legs})
    parts = dict.fromkeys(PARTS, 0.0)
    gross = sum(leg.qty * CONTRACT * ((exit_prices or {}).get(leg.occ, _intrinsic(leg, settle or 0.0))
                                     - entry[leg.occ]) for leg in legs)
    fees = gross - total                              # what the recorded total paid besides the price change
    if path:
        parts["costs"] -= sum(leg.qty * CONTRACT * (entry[leg.occ] - path[0].mids[leg.occ]) for leg in legs)
        if exit_prices is not None:
            parts["costs"] -= sum(leg.qty * CONTRACT * (path[-1].mids[leg.occ] - exit_prices[leg.occ]) for leg in legs)
    parts["costs"] -= fees
    steps = list(pairwise(path))
    if end is not None and path:
        steps.append((path[-1], end))
    for a, b in steps:
        days = max(0, ((b.day if b.day != date.max else max(leg.expiration for leg in legs)) - a.day).days)
        ds = b.s - a.s
        for leg in legs:
            iv = a.ivs.get(leg.occ)
            if iv is None or a.day >= leg.expiration:
                continue
            g = opt.greeks(leg.kind, a.s, leg.strike, opt.year_fraction(a.day, leg.expiration), iv, rate)
            n = leg.qty * CONTRACT
            parts["direction"] += n * (g.delta * ds + 0.5 * g.gamma * ds * ds)
            parts["time"] += n * g.theta_per_day * days
            iv_b = b.ivs.get(leg.occ)
            if iv_b is not None:
                parts["volatility"] += n * g.vega_per_point * (iv_b - iv) * 100
    parts["unexplained"] = total - sum(parts[k] for k in PARTS if k != "unexplained")
    return {"total": round(total, 2), **{k: round(v, 2) for k, v in parts.items()}, "points": len(path),
            "steps": len(steps), "notes": notes, "version": VERSION}


# ── sources ──────────────────────────────────────────────────────────────────────────────────────
def _rate(market: sqlite3.Connection, day: date) -> float:
    row = market.execute("SELECT value FROM stk_rates WHERE series IN ('UST3M', 'UST3M@schwab') AND day<=? "
                         "ORDER BY day DESC LIMIT 1", (day.toordinal(),)).fetchone()
    return float(row[0]) / 100 if row else DEFAULT_RATE


def chain_points(market: sqlite3.Connection, symbol: str, legs: Sequence[Leg], first: date, last: date,
                 rate: float) -> list[Point]:
    """Each stored session's chain from ``first`` to ``last``: the legs' mids (two-sided quotes only) and their
    implied volatilities, with the underlying price recorded with the chain (else the day's close)."""
    from desk.stocks.repo import _unpack

    wanted = {leg.occ: leg for leg in legs}
    expirations = sorted({leg.expiration.toordinal() for leg in legs})
    marks = ",".join("?" for _ in expirations)
    by: dict[int, Point] = {}
    for day, price, blob in market.execute(
            f"SELECT day, underlying_price, chain_zlib FROM stk_option_chains WHERE underlying=? AND expiration IN "  # noqa: S608
            f"({marks}) AND day BETWEEN ? AND ? ORDER BY day", (symbol, *expirations, first.toordinal(),
                                                                last.toordinal())):
        d = date.fromordinal(int(day))
        point = by.setdefault(int(day), Point(d, float(price or 0.0), {}))
        for q in _unpack(blob):
            if q[0] in wanted and q[3] is not None and q[4] is not None and 0 < q[3] <= q[4]:
                point.mids[q[0]] = (float(q[3]) + float(q[4])) / 2
    out = []
    for day, point in sorted(by.items()):
        if point.s <= 0:
            row = market.execute("SELECT close FROM stk_bars WHERE symbol=? AND day=?", (symbol, day)).fetchone()
            point.s = float(row[0]) if row else 0.0
        if point.s <= 0:
            continue
        point.ivs = {occ: _iv(wanted[occ], mid, point.s, point.day, rate) for occ, mid in point.mids.items()}
        out.append(point)
    return out


def _split_between(market: sqlite3.Connection, symbol: str, first: date, last: date) -> bool:
    return market.execute("SELECT 1 FROM stk_splits WHERE symbol=? AND execution_date>? AND execution_date<=? LIMIT 1",
                          (symbol, first.toordinal(), last.toordinal())).fetchone() is not None


def paper_trade(market: sqlite3.Connection, p: Any, evidence: Mapping[str, Any]) -> dict[str, Any] | None:
    """A closed PAPER option position's split, or None when it cannot be graded (a modelled exit, a split, no
    recorded P/L)."""
    if p.asset != "option" or p.pnl is None or p.exit_day is None or evidence.get("modeled_exit"):
        return None
    if (p.exit_reason or "").startswith("underlying split") or _split_between(market, p.symbol, p.entry_day, p.exit_day):
        return None
    legs = [Leg(leg["occ"], leg["kind"], float(leg["strike"]), date.fromisoformat(leg["expiration"]),
                float(leg["quantity"])) for leg in p.legs]
    entry = {leg["occ"]: float(leg["entry_price"]) for leg in p.legs}
    exits = {leg["occ"]: float(leg["exit_price"]) for leg in evidence.get("legs") or [] if "exit_price" in leg}
    settled = not exits
    settle = None
    if settled:
        if (p.exit_reason or "").startswith("written off"):
            exits = {leg.occ: 0.0 for leg in legs}
            settled = False
        else:
            row = market.execute("SELECT close FROM stk_bars WHERE symbol=? AND day<=? ORDER BY day DESC LIMIT 1",
                                 (p.symbol, p.exit_day.toordinal())).fetchone()
            if row is None:
                return None
            settle = float(row[0])
    elif set(exits) != set(entry):
        return None
    rate = _rate(market, p.entry_day)
    points = chain_points(market, p.symbol, legs, p.entry_day, p.exit_day, rate)
    return split(legs, points, entry, float(p.pnl), None if settled else exits, settle, rate)


def play_trade(research: sqlite3.Connection, play: Mapping[str, Any], outcome: Mapping[str, Any],
               rate: float = DEFAULT_RATE) -> dict[str, Any] | None:
    """A graded play's split (one contract a leg), from its decision quotes, its legs' quoted closing marks
    (``stk_track_days``, status quoted) and its settlement at expiry."""
    raw = json.loads(play["legs_json"])
    if not raw:
        return None
    session = date.fromordinal(int(play["session"]))
    legs = [Leg(x["occ"], x["kind"], float(x["strike"]), date.fromisoformat(x["expiration"]),
                1.0 if x["side"] == "buy" else -1.0) for x in raw]
    entry = {x["occ"]: float(x["ask"] if x["side"] == "buy" else x["bid"]) for x in raw}
    start = Point(session, float(play["underlying"]), {x["occ"]: float(x["mid"]) for x in raw if x.get("mid")},
                  {x["occ"]: (float(x["iv"]) if x.get("iv") else None) for x in raw})
    marks: dict[int, dict[str, tuple[float, float | None, float | None]]] = defaultdict(dict)
    occs = [leg.occ for leg in legs]
    holes = ",".join("?" for _ in occs)
    for occ, day, bid, ask, mark, iv, s in research.execute(
            f"SELECT occ, session, bid, ask, mark, iv, underlying FROM stk_track_days WHERE status='quoted' AND "  # noqa: S608
            f"occ IN ({holes}) AND session>? AND session<?", (*occs, int(play["session"]), int(play["expiration"]))):
        mid = (float(bid) + float(ask)) / 2 if bid is not None and ask is not None else mark
        if mid is not None:
            marks[int(day)][occ] = (float(mid), None if iv is None else float(iv), None if s is None else float(s))
    points = [start]
    for day in sorted(marks):
        got = marks[day]
        s = next((v[2] for v in got.values() if v[2]), None)
        if s is None or len(got) < len(legs):
            continue
        points.append(Point(date.fromordinal(day), s, {k: v[0] for k, v in got.items()},
                            {k: v[1] for k, v in got.items()}))
    total = float(outcome["net_return"]) * float(play["max_loss"])
    return split(legs, points, entry, total, None, float(outcome["underlying_close"]), rate)


# ── the store ────────────────────────────────────────────────────────────────────────────────────
def _save(research: sqlite3.Connection, kind: str, ref: str, group: str, symbol: str, structure: str, day: date,
          parts: Mapping[str, Any], now: datetime) -> None:
    research.execute("INSERT OR IGNORE INTO stk_pl_attribution(kind, ref, version, grp, symbol, structure, closed_day, "
                     "total, direction, volatility, time, costs, unexplained, detail_json, computed_at) VALUES "
                     "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (kind, ref, VERSION, group, symbol, structure, day.toordinal(), parts["total"], parts["direction"],
                      parts["volatility"], parts["time"], parts["costs"], parts["unexplained"],
                      json.dumps({k: parts[k] for k in ("points", "steps", "notes")}, allow_nan=False), to_iso(now)))


def _skip(research: sqlite3.Connection, kind: str, ref: str, group: str, symbol: str, structure: str, day: date,
          why: str, now: datetime) -> None:
    research.execute("INSERT OR IGNORE INTO stk_pl_attribution(kind, ref, version, grp, symbol, structure, closed_day, "
                     "total, direction, volatility, time, costs, unexplained, detail_json, computed_at) VALUES "
                     "(?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,?,?)",
                     (kind, ref, VERSION, group, symbol, structure, day.toordinal(), json.dumps({"skipped": why}),
                      to_iso(now)))


def run(market: sqlite3.Connection, research: sqlite3.Connection, ledger: sqlite3.Connection | None, now: datetime,
        *, limit: int = PER_PASS) -> dict[str, Any]:
    """Attribute closed PAPER option trades and graded plays not attributed yet (at most ``limit``; idempotent)."""
    from desk.stocks.paper.book import PaperBook

    try:
        done = {(k, r) for k, r in research.execute("SELECT kind, ref FROM stk_pl_attribution WHERE version=?",
                                                    (VERSION,))}
    except sqlite3.OperationalError:
        return {"attributed": 0, "reason": "no attribution table"}
    out = {"paper": 0, "plays": 0, "skipped": 0}
    budget = limit
    if ledger is not None:
        book = PaperBook(ledger)
        rows = ledger.execute("SELECT id, exit_evidence_json FROM stk_positions WHERE state='CLOSED' AND asset='option' "
                              "ORDER BY exit_day, id").fetchall()
        todo = [r for r in rows if ("paper", r[0]) not in done][:budget]
        if todo:
            ids = {r[0] for r in todo}
            evidence = {r[0]: json.loads(r[1] or "{}") for r in todo}
            for p in [p for p in book.positions(state="CLOSED") if p.id in ids]:
                parts = paper_trade(market, p, evidence[p.id])
                assert p.exit_day is not None
                if parts is None:
                    _skip(research, "paper", p.id, p.account, p.symbol, p.structure, p.exit_day,
                          "a modelled exit, a split or no recorded result", now)
                    out["skipped"] += 1
                else:
                    _save(research, "paper", p.id, p.account, p.symbol, p.structure, p.exit_day, parts, now)
                    out["paper"] += 1
                budget -= 1
    if budget > 0:
        for r in research.execute(
                "SELECT p.session, p.category, p.rank, p.symbol, p.structure, p.expiration, p.legs_json, p.underlying, "
                "p.max_loss, o.net_return, o.underlying_close FROM stk_plays p JOIN stk_play_outcomes o "
                "USING (session, category, rank) ORDER BY p.session, p.category, p.rank").fetchall():
            ref = f"{r['session']}:{r['category']}:{r['rank']}"
            if ("play", ref) in done:
                continue
            if budget <= 0:
                break
            budget -= 1
            parts = play_trade(research, r, r, _rate(market, date.fromordinal(int(r["session"]))))
            day = date.fromordinal(int(r["expiration"]))
            if parts is None:
                _skip(research, "play", ref, r["category"], r["symbol"], r["structure"], day, "no legs", now)
                out["skipped"] += 1
            else:
                _save(research, "play", ref, r["category"], r["symbol"], r["structure"], day, parts, now)
                out["plays"] += 1
    return out


def of_trade(research: sqlite3.Connection, kind: str, ref: str) -> dict[str, Any] | None:
    """One trade's split as stored (None when not attributed yet, or left out)."""
    try:
        row = research.execute("SELECT * FROM stk_pl_attribution WHERE kind=? AND ref=? AND version=?",
                               (kind, ref, VERSION)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None or row["total"] is None:
        return None
    return {**{k: row[k] for k in ("total", *PARTS)}, **json.loads(row["detail_json"]),
            "words": words(dict(row))}


def words(parts: Mapping[str, Any]) -> str:
    """"+$420 from time decay, −$610 from direction moves, −$95 costs, +$12 unexplained" (largest first)."""
    bits = []
    for k in sorted(PARTS, key=lambda k: -abs(float(parts.get(k) or 0.0))):
        v = float(parts.get(k) or 0.0)
        if abs(v) < 0.5:
            continue
        sign = "+" if v >= 0 else "−"
        bits.append(f"{sign}${abs(v):,.0f} {'from ' if k not in ('costs', 'unexplained') else ''}{WORDS[k]}")
    return ", ".join(bits) if bits else "nothing to split"


def summary(research: sqlite3.Connection, titles: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """By strategy (PAPER) or section (plays): trades, the sum of each part, and the parts that were consistent (the
    same sign in at least ``CONSISTENT`` of at least ``MIN_TRADES`` trades)."""
    try:
        rows = research.execute("SELECT * FROM stk_pl_attribution WHERE version=? AND total IS NOT NULL",
                                (VERSION,)).fetchall()
    except sqlite3.OperationalError:
        return []
    by: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by[(r["kind"], r["grp"])].append(r)
    out: list[dict[str, Any]] = []
    for (kind, group), items in by.items():
        sums = {k: round(sum(float(r[k]) for r in items), 2) for k in ("total", *PARTS)}
        consistent = []
        if len(items) >= MIN_TRADES:
            for k in PARTS[:-1]:
                values = [float(r[k]) for r in items if abs(float(r[k])) >= 0.5]
                if not values:
                    continue
                share = sum((v > 0) == (sums[k] > 0) for v in values) / len(items)
                if share >= CONSISTENT and abs(sums[k]) >= 1:
                    consistent.append({"part": k, "sum": sums[k], "share": round(share, 3),
                                       "mean": round(fmean(float(r[k]) for r in items), 2)})
        out.append({"kind": kind, "group": group, "title": (titles or {}).get(group, group), "trades": len(items),
                    **sums, "words": words(sums), "consistent": consistent})
    out.sort(key=lambda g: (-int(g["trades"]), str(g["group"])))
    return out


__all__ = ["CONSISTENT", "MIN_TRADES", "PARTS", "VERSION", "Leg", "Point", "chain_points", "of_trade", "paper_trade",
           "play_trade", "run", "split", "summary", "words"]
