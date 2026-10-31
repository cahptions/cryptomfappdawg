"""All identified plays, including ones absent from the first frozen closing snapshot.

Preserve closing cohorts for performance/learning. Recover only genuine stored first sightings; never rebuild old
recommendations with today's model or fabricate option prices. No personal selection or brokerage action required.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from desk.core.clock import to_iso
from desk.stocks import calendar as cal
from desk.stocks import play_history as ph
from desk.stocks import play_selections as ps
from desk.stocks.play_tracking import get_state, legs_key
from desk.stocks.plays import CATEGORIES, TYPES, structure_of
from desk.storage.db import transaction

ARCHIVE_CATEGORY = "archive_"
RECOVERY_METHOD = "saved-first-sighting-v1"


def recovery_pending(research: sqlite3.Connection, *, count: bool = True) -> list[sqlite3.Row] | int:
    query = ("FROM stk_play_first_seen s WHERE NOT EXISTS (SELECT 1 FROM stk_play_ideas i WHERE "
             "i.session=s.session AND i.category=s.category AND i.legs_key=s.key AND i.identified_at<=s.seen_at) "
             "AND NOT EXISTS (SELECT 1 FROM stk_play_archive_skips q WHERE q.session=s.session "
             "AND q.category=s.category AND q.key=s.key)")
    if count:
        return int(research.execute("SELECT COUNT(*) " + query).fetchone()[0])
    return research.execute("SELECT s.* " + query + " ORDER BY seen_at DESC LIMIT 500").fetchall()


def recovered_play(row: sqlite3.Row) -> dict[str, Any]:
    """The original sighting retains contracts and entry quotes, but not its ranking/probability model."""
    from desk.stocks.play_tracking import parse_occ

    legs = json.loads(row["legs_json"])
    if len(legs) not in (1, 2, 4):
        raise ValueError("Unsupported saved structure")
    for leg in legs:
        symbol, kind, strike, expiration = parse_occ(leg["occ"])
        if (symbol != row["symbol"] or kind != leg["kind"] or strike != leg["strike"]
                or expiration.isoformat() != leg["expiration"] or leg["side"] not in {"buy", "sell"}):
            raise ValueError("Saved contract identity mismatch")
    structure = structure_of(legs)
    action, kind_label = TYPES[structure]
    entry = float(row["entry"])
    if not math.isfinite(entry) or entry <= 0 or len({leg["expiration"] for leg in legs}) != 1:
        raise ValueError("Invalid saved entry")
    risk = ph.basis_loss(action, entry, legs)
    if risk <= 0:
        raise ValueError("Invalid saved risk")
    underlying = next((leg.get("underlying") for leg in legs if leg.get("underlying")), None)
    return {"symbol": row["symbol"], "structure": structure, "type": kind_label, "action": action, "rank": None,
            "legs": legs, "expiration": legs[0]["expiration"], "strike": legs[0]["strike"], "entry": entry,
            "max_loss": risk, "cost": risk if action == "BUY" else None, "underlying_price": underlying,
            "source": "Original saved first-sighting quotes", "quoted_at": None, "recovered_sighting": True,
            "reasons": ["Recovered from the original saved sighting. Original model odds and rank were not retained."]}


def recover(research: sqlite3.Connection, now: datetime) -> dict[str, int]:
    rows = recovery_pending(research, count=False)
    assert isinstance(rows, list)
    added = skipped = 0
    for row in rows:
        try:
            play = recovered_play(row)
            board = {"asof": date.fromordinal(row["session"]).isoformat(), "method": RECOVERY_METHOD,
                     "generated_at": row["seen_at"], "categories": [{"id": row["category"], "plays": [play]}]}
            ps.register(research, board, now)
            added += 1
        except (ValueError, TypeError, KeyError):
            with transaction(research):
                research.execute("INSERT OR IGNORE INTO stk_play_archive_skips VALUES (?,?,?,?,?)",
                                 (row["session"], row["category"], row["key"],
                                  "Original sighting is incomplete or has an unsupported contract structure", to_iso(now)))
            skipped += 1
    return {"recovered": added, "skipped": skipped}


def sessions(research: sqlite3.Connection) -> dict[str, Any]:
    # Group by the day actually identified in New York, not by an old quote session on a stale board.
    keys: dict[date, set[tuple[str, str]]] = defaultdict(set)
    for row in research.execute("SELECT session,category,legs_json FROM stk_plays"):
        keys[date.fromordinal(row["session"])].add((row["category"], legs_key(json.loads(row["legs_json"]))))
    for row in research.execute("SELECT identified_at,category,legs_key FROM stk_play_ideas"):
        keys[cal.new_york(datetime.fromisoformat(row["identified_at"])).date()].add((row["category"], row["legs_key"]))
    return {"version": 2, "sessions": [{"session": day.isoformat(), "plays": len(keys[day])}
            for day in sorted(keys, reverse=True)], "repair": get_state(research, "repair")}


def resolve(research: sqlite3.Connection, when: str, now: datetime) -> date | None:
    available = [date.fromisoformat(r["session"]) for r in sessions(research)["sessions"]]
    today = cal.new_york(now).date()
    offsets = {"week": 7, "month": 30, "quarter": 91, "year": 365}
    wanted = today if when == "latest" else cal.previous_trading_day(today) if when == "yesterday" else (
        today-timedelta(days=offsets[when]) if when in offsets else date.fromisoformat(when))
    return next((day for day in available if day <= wanted), min(available) if available else None)


def _idea_row(research: sqlite3.Connection, record: sqlite3.Row, now: datetime) -> dict[str, Any]:
    data = ps.view(research, record["id"], now)
    play, chart = data["idea"]["play"], data["chart"]
    points = sorted([*chart["daily"], *chart["intraday"]], key=ph.point_time)
    last = points[-1] if points else None
    latest = None if last is None else {"day": cal.new_york(ph.point_time(last)).date().isoformat(),
        "at": last["at"], "status": last["status"], "value": last["value"],
        "pnl": last["close"]["pnl"], "pct": last["close"]["pct"], "provisional": last["status"] == "provisional"}
    normalized = None if last is None else {"status": last["status"], "pnl": last["close"]["pnl"], "value": last["value"]}
    state, text = ph._state(play, normalized, chart.get("quarantine"))
    if data["tracking"] is None and chart["expired"] and state in {"open", "waiting"}:
        text = "Expired; awaiting expiry close"
    pcts = [p["close"]["pct"] for p in points if p["status"] in {"quoted", "live", "settled"}]
    identified = cal.new_york(datetime.fromisoformat(record["identified_at"])).date()
    return {"category": record["category"], "rank": play.get("rank"), "symbol": play["symbol"],
        "archive_id": record["id"], "structure": play["structure"], "action": play["action"], "type": play["type"],
        "label": ph.label(play), "expiration": play["expiration"], "entry": play["entry"],
        "entry_basis": "First identified", "identified_at": record["identified_at"], "cost": play["max_loss"],
        "latest": latest, "state": state, "state_label": text, "first_seen": None, "twin": None,
        "peak": max(pcts) if pcts else None, "worst": min(pcts) if pcts else None,
        "days_held": max(0, len(cal.trading_days(identified, ph.point_time(last).date()))-1) if last else 0,
        "spark": [[p["at"],p["close"]["pct"],p["status"]] for p in points][-ph.SPARK_POINTS:],
        "method": record["method"], "chance": play.get("itm_kestrel"), "chance_label": play.get("chance_label")}


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    result = ph._summary(rows)
    if result and any(row.get("archive_id") for row in rows):
        result["twins_average"] = None  # all discoveries cannot be compared with a closing-only twin subset
    return result


def session_view(research: sqlite3.Connection, session: date, now: datetime) -> dict[str, Any]:
    closing = ph.session_view(research, session, now)
    keys = {(r["category"],r["rank"]):legs_key(json.loads(r["legs_json"])) for r in research.execute(
        "SELECT category,rank,legs_json FROM stk_plays WHERE session=?", (session.toordinal(),))}
    rows = {(r["category"],keys[(r["category"],r["rank"])]):r
            for category in closing["categories"] for r in category["plays"]}
    closed_keys = set(rows)
    start = datetime.combine(session,time(),cal.NEW_YORK)
    end = datetime.combine(session+timedelta(days=1),time(),cal.NEW_YORK)
    ideas = research.execute("SELECT * FROM stk_play_ideas WHERE identified_at>=? AND identified_at<? "
                            "ORDER BY identified_at,id", (to_iso(start),to_iso(end)))
    chosen = set()
    for record in ideas:
        key = (record["category"],record["legs_key"])
        if key in chosen:
            continue
        chosen.add(key)
        # A record first built after the close adds no earlier sighting for an existing closing pick.
        if key in closed_keys and datetime.fromisoformat(record["identified_at"]) >= cal.session_close(session):
            continue
        rows[key] = _idea_row(research,record,now)
    categories: list[dict[str, Any]] = []
    for category in CATEGORIES:
        plays = sorted((r for r in rows.values() if r["category"] == category.id),
                       key=lambda r:(r.get("identified_at") or "",r["symbol"],r.get("archive_id") or str(r["rank"])))
        if plays:
            categories.append({"id":category.id,"title":category.title,"emoji":category.emoji,
                               "plays":plays,"summary":_summary(plays)})
    every = [r for c in categories for r in c["plays"]]
    valued = sum(r["latest"] is not None for r in every)
    return {**closing,"version":2,"scope":"all","categories":categories,"summary":_summary(every),
            "methods":sorted({r["method"] for r in every if r["method"] != RECOVERY_METHOD}),
            "coverage":{"identified":len(every),"closing":len(closed_keys),"valued":valued,
                        "waiting":len(every)-valued,"recovery_pending":recovery_pending(research)},
            "message":"All saved identified plays; manual confirmations are separate."}
