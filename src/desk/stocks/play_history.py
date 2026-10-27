"""Plays history views (docs/STOCKS_PLAYS.md → History). Read-only: stored picks, marks and quotes, light arithmetic.

Every percentage is on the money at risk after fees (``desk.stocks.play_marks.profit``). Two bases are shown: **at
the close** (the recorded entry: the ask of the session's last quotes) and **when first shown** (the ask when the
play first appeared on the live board that session). Marks are permanent; *provisional* gap estimates, intraday
points and today's live quotes are shown with their status so the page can draw them dashed.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from desk.stocks import calendar as cal
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT as FEE
from desk.stocks.play_marks import profit, settle_day
from desk.stocks.play_tracking import get_state, legs_key, unpack
from desk.stocks.plays import BY_CATEGORY, CATEGORIES, TYPES
from desk.stocks.repo import day_of

VERSION = 1
SPARK_POINTS = 60


def _iso(n: int) -> str:
    return date.fromordinal(n).isoformat()


def sessions(research: sqlite3.Connection) -> dict[str, Any]:
    rows = research.execute("SELECT session, COUNT(*) n FROM stk_plays GROUP BY session ORDER BY session DESC").fetchall()
    return {"version": VERSION, "sessions": [{"session": _iso(r["session"]), "plays": r["n"]} for r in rows],
            "repair": get_state(research, "repair")}


# ── values and P/L ───────────────────────────────────────────────────────────────────────────────
def _width(legs: list[dict[str, Any]]) -> float:
    strikes = [leg["strike"] for leg in legs]
    return max(strikes) - min(strikes) if len(legs) == 2 and legs[0]["kind"] == legs[1]["kind"] else 0.0


def basis_loss(action: str, entry: float, legs: list[dict[str, Any]]) -> float:
    """The money at risk per contract for an entry price (a spread's worst case depends on the credit taken)."""
    n = len(legs)
    return entry * 100 + FEE * n if action == "BUY" else (_width(legs) - entry) * 100 + FEE * n


def pnl(action: str, entry: float, legs: list[dict[str, Any]], value: float, settled: bool) -> dict[str, float]:
    p, pct = profit(action, entry, basis_loss(action, entry, legs), value, len(legs), settled=settled)
    return {"pnl": round(p, 2), "pct": round(pct, 5)}


def live_values(research: sqlite3.Connection, plays: list[dict[str, Any]], today: date) -> dict[str, dict[str, Any]]:
    """Today's latest quotes of each play's legs (before the close folds them): legs key → value and time."""
    occs = sorted({leg["occ"] for p in plays for leg in p["legs"]})
    latest: dict[str, sqlite3.Row] = {}
    for i in range(0, len(occs), 400):
        chunk = occs[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for r in research.execute(f"SELECT occ, at, bid, ask, underlying FROM stk_track_ticks WHERE session = ? AND "  # noqa: S608
                                  f"occ IN ({marks}) ORDER BY at", (day_of(today), *chunk)):
            latest[r["occ"]] = r
    out = {}
    for p in plays:
        rows = [latest.get(leg["occ"]) for leg in p["legs"]]
        if any(r is None or r["bid"] is None or r["ask"] is None for r in rows):
            continue
        value = sum(r["bid"] if leg["side"] == "buy" else -r["ask"] for leg, r in zip(p["legs"], rows, strict=True)
                    if r is not None)
        at = min(r["at"] for r in rows if r is not None)
        out[legs_key(p["legs"])] = {"value": round(value, 4), "at": at,
                                    "underlying": next((r["underlying"] for r in rows if r and r["underlying"]), None)}
    return out


def _state(play: dict[str, Any], last: dict[str, Any] | None, quarantine: str | None) -> tuple[str, str]:
    if quarantine:
        return "quarantined", "Quarantined: " + quarantine
    if last is None:
        return "waiting", "Waiting for its first mark"
    if last["status"] != "settled":
        return "open", "Open"
    won = last["pnl"] > 0
    if play["action"] == "SELL":
        if last["value"] == 0:
            return "won", "Expired worthless: kept the credit"
        return ("won" if won else "lost"), f"Finished in the money: {'won' if won else 'lost'}"
    if last["value"] <= 0:
        return "worthless", "Expired worthless"
    return ("won" if won else "lost"), f"Finished in the money: {'won' if won else 'lost'}"


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored = [r for r in rows if r.get("latest")]
    if not scored:
        return None
    pcts = [r["latest"]["pct"] for r in scored]
    best = max(scored, key=lambda r: r["latest"]["pct"])
    worst = min(scored, key=lambda r: r["latest"]["pct"])
    twins = [r["twin"]["pct"] for r in scored if r.get("twin") and r["twin"].get("pct") is not None]
    return {"plays": len(scored), "average": round(statistics.fmean(pcts), 5), "median": round(statistics.median(pcts), 5),
            "hit_rate": round(sum(p > 0 for p in pcts) / len(pcts), 4),
            "best": {"symbol": best["symbol"], "label": best["label"], "pct": best["latest"]["pct"]},
            "worst": {"symbol": worst["symbol"], "label": worst["label"], "pct": worst["latest"]["pct"]},
            "hundred_each": {"invested": 100 * len(pcts), "value": round(sum(100 * (1 + p) for p in pcts), 2)},
            "twins_average": round(statistics.fmean(twins), 5) if twins else None,
            "estimated": sum(r["latest"]["status"] in ("estimated", "modelled") or r["latest"].get("provisional", False)
                             for r in scored)}


def label(play: dict[str, Any]) -> str:
    """``AAL $12 call · Dec 18``, ``SPY $480/$490 put credit spread · Nov 20``."""
    kind = TYPES.get(play["structure"], (play["action"], play["structure"]))[1].lower()
    strikes = "/".join(f"${k:g}" for k in sorted({leg["strike"] for leg in play["legs"]}))
    expiry = date.fromisoformat(play["expiration"])
    return f"{play['symbol']} {strikes} {kind} · {expiry:%b} {expiry.day}"


# ── one session ──────────────────────────────────────────────────────────────────────────────────
def session_view(research: sqlite3.Connection, session: date, now: datetime) -> dict[str, Any]:
    n = day_of(session)
    plays = [dict(r) for r in research.execute("SELECT * FROM stk_plays WHERE session = ? ORDER BY category, rank", (n,))]
    if not plays:
        recorded = [r[0] for r in research.execute("SELECT DISTINCT session FROM stk_plays ORDER BY session")]
        return {"version": VERSION, "session": session.isoformat(), "categories": [], "summary": None,
                "message": "No plays were recorded for this session." if recorded else
                "No sessions are recorded yet: a session's plays are recorded once, 15 minutes after its close."}
    for p in plays:
        p["legs"] = json.loads(p["legs_json"])
        p["play"] = json.loads(p["play_json"])
        p["expiration"] = _iso(p["expiration"])
    marks: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for r in research.execute("SELECT category, rank, twin, day, status, value, pnl, pnl_pct FROM stk_play_marks WHERE "
                              "session = ? ORDER BY day", (n,)):
        marks[(r["category"], r["rank"], r["twin"])].append(dict(r))
    for r in research.execute("SELECT category, rank, twin, day, 'estimated' AS status, value, pnl, pnl_pct FROM "
                              "stk_play_marks_provisional WHERE session = ? ORDER BY day", (n,)):
        marks[(r["category"], r["rank"], r["twin"])].append({**dict(r), "provisional": True})
    first = {(r["category"], r["key"]): dict(r) for r in research.execute(
        "SELECT category, key, seen_at, entry FROM stk_play_first_seen WHERE session = ?", (n,))}
    quarantine = {(r["category"], r["rank"], r["twin"]): r["reason"] for r in research.execute(
        "SELECT category, rank, twin, reason FROM stk_play_quarantine WHERE session = ?", (n,))}
    today = cal.new_york(now).date()
    live = live_values(research, plays, today)
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in plays:
        series = sorted(marks[(p["category"], p["rank"], 0)], key=lambda m: m["day"])
        permanent = [m for m in series if not m.get("provisional")]
        last = series[-1] if series else None
        latest = None
        if last is not None:
            latest = {"day": _iso(last["day"]), "status": last["status"], "value": last["value"], "pnl": last["pnl"],
                      "pct": last["pnl_pct"], "provisional": bool(last.get("provisional"))}
        current = live.get(legs_key(p["legs"]))
        expired = settle_day(date.fromisoformat(p["expiration"])) < today
        if current and not expired and (last is None or last["day"] < day_of(today)):
            got = pnl(p["action"], p["entry"], p["legs"], current["value"], False)
            latest = {"day": today.isoformat(), "status": "live", "value": current["value"], "pnl": got["pnl"],
                      "pct": got["pct"], "at": current["at"], "provisional": False}
        pcts = [m["pnl_pct"] for m in permanent] + ([latest["pct"]] if latest and latest["status"] == "live" else [])
        seen = first.get((p["category"], legs_key(p["legs"])))
        twin_series = marks[(p["category"], p["rank"], 1)]
        state, state_label = _state(p, permanent[-1] if permanent else None, quarantine.get((p["category"], p["rank"], 0)))
        end = date.fromordinal(last["day"]) if last else session
        row = {"category": p["category"], "rank": p["rank"], "symbol": p["symbol"], "action": p["action"],
               "structure": p["structure"], "type": TYPES.get(p["structure"], ("", p["structure"]))[1],
               "label": label(p), "expiration": p["expiration"], "entry": p["entry"],
               "cost": round(basis_loss(p["action"], p["entry"], p["legs"]), 2), "latest": latest,
               "peak": round(max(pcts), 5) if pcts else None, "worst": round(min(pcts), 5) if pcts else None,
               "days_held": max(0, len(cal.trading_days(session, end)) - 1), "state": state, "state_label": state_label,
               "spark": [[_iso(m["day"]), m["pnl_pct"], "provisional" if m.get("provisional") else m["status"]]
                         for m in series][-SPARK_POINTS:],
               "first_seen": None if seen is None else {
                   "at": seen["seen_at"], "entry": seen["entry"],
                   "pct": None if latest is None else pnl(p["action"], seen["entry"], p["legs"], latest["value"],
                                                          latest["status"] == "settled")["pct"]},
               "twin": {"pct": twin_series[-1]["pnl_pct"] if twin_series else None},
               "chance": p["play"].get("itm_kestrel"), "chance_label": p["play"].get("chance_label"),
               "method": p["method"]}
        by_category[p["category"]].append(row)
    categories: list[dict[str, Any]] = []
    every: list[dict[str, Any]] = []
    for c in CATEGORIES:
        rows = by_category.get(c.id)
        if rows:
            categories.append({"id": c.id, "title": c.title, "emoji": c.emoji, "plays": rows, "summary": _summary(rows)})
            every.extend(rows)
    return {"version": VERSION, "session": session.isoformat(), "generated_at": now.isoformat(),
            "categories": categories, "summary": _summary(every), "repair": get_state(research, "repair"),
            "methods": sorted({r["method"] for r in every})}


# ── one play ─────────────────────────────────────────────────────────────────────────────────────
def _net(legs: list[dict[str, Any]], rows: dict[str, dict[str, Any]], prefix: str = "") -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for g in ("delta", "gamma", "theta", "vega"):
        values = [rows.get(leg["occ"], {}).get(prefix + g) for leg in legs]
        out[g] = None if any(v is None for v in values) else round(sum(
            (1 if leg["side"] == "buy" else -1) * float(v) for leg, v in zip(legs, values, strict=True)
            if v is not None), 5)
    ivs = [rows.get(leg["occ"], {}).get(prefix + "iv") for leg in legs]
    out["iv"] = None if not ivs or ivs[0] is None else round(float(ivs[0]), 4)
    return out


def _combine(legs: list[dict[str, Any]], by_leg: dict[str, list[tuple[datetime, float, float, float | None]]],
             status: dict[str, str]) -> list[tuple[datetime, float, float | None, str]]:
    """Merge legs' quote paths: a point wherever every leg has a quote so far (each carried forward)."""
    times = sorted({t for occ in by_leg for t, *_ in by_leg[occ]})
    cursor = {occ: 0 for occ in by_leg}
    current: dict[str, tuple[float, float, float | None]] = {}
    out = []
    for t in times:
        for occ, points in by_leg.items():
            while cursor[occ] < len(points) and points[cursor[occ]][0] <= t:
                _, bid, ask, s = points[cursor[occ]]
                current[occ] = (bid, ask, s)
                cursor[occ] += 1
        if len(current) < len(legs):
            continue
        value = sum(current[leg["occ"]][0] if leg["side"] == "buy" else -current[leg["occ"]][1] for leg in legs)
        s = next((current[leg["occ"]][2] for leg in legs if current[leg["occ"]][2]), None)
        st = "estimated" if any(status.get(leg["occ"]) == "estimated" for leg in legs) else "quoted"
        out.append((t, value, s, st))
    return out


def play_view(research: sqlite3.Connection, session: date, category: str, rank: int, now: datetime) -> dict[str, Any]:
    n = day_of(session)
    row = research.execute("SELECT * FROM stk_plays WHERE session = ? AND category = ? AND rank = ?",
                           (n, category, rank)).fetchone()
    if row is None:
        return {"version": VERSION, "found": False}
    play = json.loads(row["play_json"])
    legs = json.loads(row["legs_json"])
    action, entry = row["action"], row["entry"]
    seen = research.execute("SELECT seen_at, entry, legs_json FROM stk_play_first_seen WHERE session = ? AND "
                            "category = ? AND key = ?", (n, category, legs_key(legs))).fetchone()
    bases: dict[str, float] = {"close": entry}
    if seen is not None:
        bases["first"] = seen["entry"]

    def point(t: str, value: float, status: str, s: float | None, settled: bool = False, note: str | None = None,
              stored: sqlite3.Row | None = None) -> dict[str, Any]:
        out = {"t": t, "value": round(value, 4), "status": status, "underlying": s, "note": note,
               **{b: pnl(action, e, legs, value, settled) for b, e in bases.items()}}
        if stored is not None:                 # the recorded mark is the official close-basis figure
            out["close"] = {"pnl": stored["pnl"], "pct": stored["pnl_pct"]}
        return out

    daily = [point(_iso(r["day"]), r["value"], r["status"], r["underlying"], r["status"] == "settled", r["note"], r)
             for r in research.execute("SELECT * FROM stk_play_marks WHERE session = ? AND category = ? AND rank = ? "
                                       "AND twin = 0 ORDER BY day", (n, category, rank))]
    marked = {p["t"] for p in daily}
    daily += [point(_iso(r["day"]), r["value"], "provisional", r["underlying"], note=r["note"], stored=r)
              for r in research.execute("SELECT * FROM stk_play_marks_provisional WHERE session = ? AND category = ? "
                                        "AND rank = ? AND twin = 0 ORDER BY day", (n, category, rank))
              if _iso(r["day"]) not in marked]
    daily.sort(key=lambda p: p["t"])
    # intraday: stored five-minute paths (kept 30+ days), then today's live quotes
    occs = [leg["occ"] for leg in legs]
    marks = ",".join("?" * len(occs))
    paths: dict[int, dict[str, list[tuple[datetime, float, float, float | None]]]] = defaultdict(lambda: defaultdict(list))
    status: dict[int, dict[str, str]] = defaultdict(dict)
    for r in research.execute(f"SELECT occ, session, status, packed FROM stk_track_intraday WHERE occ IN ({marks}) "  # noqa: S608
                              "AND session >= ? ORDER BY session", (*occs, n)):
        status[r["session"]][r["occ"]] = r["status"]
        for minute, bid, ask, s, _ in unpack(r["packed"]):
            if minute is None or bid is None or ask is None:
                continue
            at = datetime.combine(date.fromordinal(r["session"]), time(int(minute) // 60, int(minute) % 60),
                                  cal.NEW_YORK)
            paths[r["session"]][r["occ"]].append((at, float(bid), float(ask), s))
    today = cal.new_york(now).date()
    for r in research.execute(f"SELECT occ, at, bid, ask, underlying FROM stk_track_ticks WHERE occ IN ({marks}) AND "  # noqa: S608
                              "session = ? ORDER BY at", (*occs, day_of(today))):
        if r["bid"] is not None and r["ask"] is not None:
            paths[day_of(today)][r["occ"]].append((datetime.fromisoformat(r["at"]), r["bid"], r["ask"], r["underlying"]))
            status[day_of(today)].setdefault(r["occ"], "live")
    intraday = []
    for day in sorted(paths):
        if day < n or len(paths[day]) < len(legs):
            continue
        for t, value, s, st in _combine(legs, paths[day], status[day]):
            st = "live" if day == day_of(today) and st == "quoted" and all(
                status[day].get(o) == "live" for o in occs) else st
            intraday.append(point(t.isoformat(), value, st, s))
    # greeks then (as recorded) and now (latest stored close, KESTREL's and Schwab's)
    latest: dict[str, dict[str, Any]] = {}
    for r in research.execute(f"SELECT * FROM stk_track_days WHERE occ IN ({marks}) ORDER BY session", occs):  # noqa: S608
        latest[r["occ"]] = dict(r)
    as_of = max((v["session"] for v in latest.values()), default=None)
    twin = research.execute("SELECT * FROM stk_play_twins WHERE session = ? AND category = ? AND rank = ?",
                            (n, category, rank)).fetchone()
    twin_view = None
    if twin is not None:
        twin_legs = json.loads(twin["legs_json"])
        twin_view = {"symbol": twin["symbol"], "structure": twin["structure"], "expiration": _iso(twin["expiration"]),
                     "entry": twin["entry"], "legs": twin_legs,
                     "marks": [{"t": _iso(r["day"]), "pct": r["pnl_pct"], "status": r["status"]} for r in research.execute(
                         "SELECT day, status, pnl_pct FROM stk_play_marks WHERE session = ? AND category = ? AND "
                         "rank = ? AND twin = 1 ORDER BY day", (n, category, rank))]}
    outcome = research.execute("SELECT * FROM stk_play_outcomes WHERE session = ? AND category = ? AND rank = ?",
                               (n, category, rank)).fetchone()
    quarantine = research.execute("SELECT reason FROM stk_play_quarantine WHERE session = ? AND category = ? AND "
                                  "rank = ? AND twin = 0", (n, category, rank)).fetchone()
    from_legs = _net(legs, {leg["occ"]: leg for leg in legs})       # each leg's greeks as recorded
    then = {g: play.get(g) if play.get(g) is not None else from_legs.get(g)
            for g in ("iv", "delta", "gamma", "theta", "vega")}
    cat = BY_CATEGORY.get(category)
    settles = settle_day(date.fromisoformat(play["expiration"]))
    return {"version": VERSION, "found": True, "session": session.isoformat(), "category": category, "rank": rank,
            "category_title": cat.title if cat else category, "emoji": cat.emoji if cat else "",
            "label": label({**dict(row), "legs": legs, "expiration": play["expiration"]}), "play": play,
            "action": action, "legs": legs, "bases": bases, "max_loss": {b: basis_loss(action, e, legs)
                                                                       for b, e in bases.items()},
            "first_seen": None if seen is None else {"at": seen["seen_at"], "entry": seen["entry"],
                                                     "legs": json.loads(seen["legs_json"])},
            "daily": daily, "intraday": intraday, "expiration": play["expiration"], "settles": settles.isoformat(),
            "expired": settles < today or any(p["status"] == "settled" for p in daily),
            "greeks": {"then": then, "now": _net(legs, latest), "schwab_now": _net(legs, latest, "schwab_"),
                       "as_of": None if as_of is None else _iso(as_of)},
            "twin": twin_view, "quarantine": quarantine["reason"] if quarantine else None,
            "outcome": None if outcome is None else {"hit": bool(outcome["hit"]), "payoff": outcome["payoff"],
                                                    "net_return": outcome["net_return"],
                                                    "underlying_close": outcome["underlying_close"]},
            "method": row["method"], "fees_per_leg": FEE,
            "intraday_note": "Five-minute marks are kept for at least 30 days; older days show the daily close."}


def nearest_session(research: sqlite3.Connection, wanted: date) -> date | None:
    row = research.execute("SELECT MAX(session) FROM stk_plays WHERE session <= ?", (day_of(wanted),)).fetchone()
    if row is None or row[0] is None:
        row = research.execute("SELECT MIN(session) FROM stk_plays").fetchone()
    return None if row is None or row[0] is None else date.fromordinal(row[0])


def resolve(research: sqlite3.Connection, when: str, now: datetime) -> date | None:
    """Quick buttons: latest, yesterday, week, month, quarter, year — or an ISO date (the nearest recorded on or
    before it)."""
    today = cal.new_york(now).date()
    offsets = {"week": 7, "month": 30, "quarter": 91, "year": 365}
    if when == "latest":
        return nearest_session(research, today)
    if when == "yesterday":
        return nearest_session(research, cal.previous_trading_day(today))
    if when in offsets:
        return nearest_session(research, today - timedelta(days=offsets[when]))
    return nearest_session(research, date.fromisoformat(when))


__all__ = ["basis_loss", "label", "live_values", "nearest_session", "play_view", "pnl", "resolve", "session_view",
           "sessions"]
