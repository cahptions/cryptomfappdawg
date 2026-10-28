"""Personal quote-based PAPER selections. No brokerage order methods, cash transfers or research-cohort writes.

Ideas and user confirmations are immutable. Entries are captured from fresh, later received market-hour quotes;
they are never backdated to a missed opening or filled from a premarket/stale chain. Quote references are not
claims about liquidity, an actual broker fill, exercise or assignment.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from desk.core.clock import to_iso
from desk.core.errors import DeskError
from desk.stocks import calendar as cal
from desk.stocks.play_history import _combine, _net, basis_loss, exit_opportunities, label, pnl
from desk.stocks.play_marks import settle_day, settlement
from desk.stocks.play_tracking import get_state, legs_key, put_state, unpack
from desk.stocks.repo import day_of
from desk.storage.db import transaction

FRESHNESS = timedelta(minutes=2)
MAX_QUANTITY = 2**53 - 1  # interoperable integer precision, not an allocation policy


def idea_id(session: str, category: str, method: str, play: dict[str, Any]) -> str:
    value = [session, category, method, legs_key(play["legs"])]
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()[:32]


def register(research: sqlite3.Connection, board: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Freeze the first identification of an exact structure/version, and attach stable detail-page identities."""
    if not board.get("asof"):
        return board
    session, method = board["asof"], str(board.get("method", "unknown"))
    stamp = min(now, datetime.fromisoformat(board.get("generated_at") or to_iso(now)))
    with transaction(research):
        for category in board.get("categories") or []:
            for play in [*(category.get("plays") or []), *(category.get("budget_plays") or [])]:
                ident = idea_id(session, category["id"], method, play)
                play["idea_id"] = ident
                research.execute("INSERT OR IGNORE INTO stk_play_ideas VALUES (?,?,?,?,?,?,?)",
                    (ident, to_iso(stamp), day_of(date.fromisoformat(session)), category["id"], legs_key(play["legs"]),
                     json.dumps(play, allow_nan=False), method))
    return board


def calculator_chain(market: sqlite3.Connection, research: sqlite3.Connection, ident: str,
                     symbol: str, expiration: date, limit: int = 1000) -> dict[str, Any]:
    """Keep calculator legs on the idea's original session, including its exact archived contracts."""
    from desk.stocks.hub import option_table

    found = idea(research, ident)
    play = found["play"]
    if play["symbol"] != symbol:
        raise DeskError("This saved play belongs to a different stock.")
    legs = play["legs"]
    asof = date.fromordinal(found["session"])
    view = option_table(market, symbol, expiration=expiration, asof=asof, limit=limit,
                        focus=tuple(leg["occ"] for leg in legs))
    contracts = {q["occ"]: q for q in view["contracts"]}
    # Frozen idea quotes take precedence over a later snapshot from the same date.
    for leg in legs:
        if leg["expiration"] == expiration.isoformat():
            contracts[leg["occ"]] = {**leg, "quoted_at": play.get("quoted_at"),
                                     "last": None, "volume": None, "open_interest": None}
    return {**view, "contracts": list(contracts.values()), "asof": asof.isoformat(),
            "source": play["source"], "expiration": expiration.isoformat(),
            "expirations": sorted(set(view.get("expirations", []) + [leg["expiration"] for leg in legs])),
            "underlying_price": play["underlying_price"], "idea_id": ident, "stale_session": True,
            "note": "Exact play legs use their frozen idea quotes; other strikes use the same session's saved chain."}


def idea(research: sqlite3.Connection, ident: str) -> dict[str, Any]:
    row = research.execute("SELECT * FROM stk_play_ideas WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise DeskError("That play is not saved yet. Refresh Plays after the next collection pass.")
    return {**dict(row), "play": json.loads(row["play_json"])}


def next_entry(now: datetime) -> datetime:
    local = cal.new_york(now)
    opening = datetime.combine(local.date(), time(9, 30), cal.NEW_YORK)
    if cal.is_trading_day(local.date()):
        if local < opening:
            return opening
        if local < cal.session_close(local.date()):
            return now
    return datetime.combine(cal.next_trading_day(local.date()), time(9, 30), cal.NEW_YORK)


def plan(research: sqlite3.Connection, ident: str, now: datetime) -> dict[str, Any]:
    found = idea(research, ident)
    start = next_entry(now)
    expiry = settle_day(date.fromisoformat(found["play"]["expiration"]))
    return {"idea": found, "eligible_at": to_iso(start),
            "can_enter": start < cal.session_close(expiry), "mode": "market_open_or_next_fresh_quote",
            "timing_policy": "Opening session is the default; no validated adaptive entry-timing policy is active.",
            "note": "Premarket selections wait for the opening session. After-open confirmations use a new quote; "
                    "a missed opening is never reconstructed as an option fill. No brokerage order is sent."}


def confirm(research: sqlite3.Connection, ident: str, quantity: int, request_id: str,
            now: datetime) -> dict[str, Any]:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= MAX_QUANTITY:
        raise DeskError("Choose a positive whole number of contracts within integer precision.")
    try:
        uuid.UUID(request_id)
    except ValueError as exc:
        raise DeskError("Invalid confirmation request; reopen the confirmation window.") from exc
    with transaction(research):
        previous = research.execute("SELECT * FROM stk_play_selections WHERE request_id=?", (request_id,)).fetchone()
        if previous is not None:
            if previous["idea_id"] != ident or previous["quantity"] != quantity:
                raise DeskError("This confirmation was already used for a different selection.")
            return dict(previous)
        prepared = plan(research, ident, now)
        if not prepared["can_enter"]:
            raise DeskError("This contract expires before a new entry is possible. Choose an unexpired play.")
        ticket = uuid.uuid4().hex
        research.execute("INSERT INTO stk_play_selections VALUES (?,?,?,?,?,?)",
                         (ticket, request_id, ident, to_iso(now), prepared["eligible_at"], quantity))
        return dict(research.execute("SELECT * FROM stk_play_selections WHERE id=?", (ticket,)).fetchone())


def _event(research: sqlite3.Connection, ident: str, kind: str) -> dict[str, Any] | None:
    row = research.execute("SELECT at, detail_json FROM stk_play_selection_events WHERE selection_id=? AND kind=?",
                           (ident, kind)).fetchone()
    return None if row is None else {"at": row["at"], **json.loads(row["detail_json"])}


def _save(research: sqlite3.Connection, ident: str, kind: str, at: datetime, details: dict[str, Any]) -> None:
    research.execute("INSERT OR IGNORE INTO stk_play_selection_events(selection_id,kind,at,detail_json) VALUES (?,?,?,?)",
                     (ident, kind, to_iso(at), json.dumps(details, allow_nan=False)))


def ticket(research: sqlite3.Connection, ident: str, now: datetime) -> dict[str, Any]:
    row = research.execute("SELECT * FROM stk_play_selections WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise DeskError("That tracked play was not found.")
    found = idea(research, row["idea_id"])
    entry, closed = _event(research, ident, "ENTRY"), _event(research, ident, "CLOSE")
    cancelled = _event(research, ident, "CANCEL") or _event(research, ident, "EXPIRED_NO_ENTRY")
    requested = _event(research, ident, "CLOSE_REQUEST")
    state = "closed" if closed else "cancelled" if cancelled else "closing" if requested else "open" if entry else "queued"
    expired = cal.session_close(settle_day(date.fromisoformat(found["play"]["expiration"]))) <= now
    message = {"closed": "Closed PAPER reference; original quotes and history are preserved.",
               "cancelled": "Cancelled without a PAPER entry.",
               "closing": "Waiting for a fresh market-hours quote to record the PAPER exit.",
               "open": "Tracking from the captured PAPER reference entry.",
               "queued": "Waiting for the opening session and a fresh two-sided quote."}[state]
    if state == "queued" and datetime.fromisoformat(row["eligible_at"]) <= now:
        message = "Waiting for a new, fresh, timestamped quote. Stale or premarket quotes cannot create an entry."
    if expired and state in {"open", "closing"}:
        message = "Expired; waiting for the official underlying close to show an expiry comparison."
    return {**dict(row), "idea": found, "entry": entry, "exit": closed, "state": state,
            "message": message, "cancelled": cancelled, "expired": expired, "orders_enabled": False}


def action(research: sqlite3.Connection, ident: str, kind: str, now: datetime) -> dict[str, Any]:
    with transaction(research):
        current = ticket(research, ident, now)
        if kind == "cancel" and current["state"] == "queued":
            _save(research, ident, "CANCEL", now, {"reason": "Operator cancelled before entry"})
        elif kind == "close" and current["state"] in {"open", "closing"}:
            _save(research, ident, "CLOSE_REQUEST", now, {"reason": "Operator requested a PAPER exit"})
        elif current["state"] not in {"closed", "cancelled"}:
            raise DeskError("Queued selections can be cancelled; entered selections use Close PAPER tracking.")
    return ticket(research, ident, now)


def active_legs(research: sqlite3.Connection, today: date) -> list[dict[str, Any]]:
    rows = research.execute("SELECT i.play_json FROM stk_play_selections p JOIN stk_play_ideas i ON i.id=p.idea_id "
        "WHERE NOT EXISTS (SELECT 1 FROM stk_play_selection_events e WHERE e.selection_id=p.id "
        "AND e.kind IN ('CLOSE','CANCEL','EXPIRED_NO_ENTRY'))")
    return [leg for row in rows for leg in json.loads(row[0])["legs"] if date.fromisoformat(leg["expiration"]) >= today]


def _quotes(research: sqlite3.Connection, legs: list[dict[str, Any]], after: datetime,
            now: datetime) -> list[dict[str, Any]] | None:
    quotes = []
    for leg in legs:
        row = research.execute("SELECT * FROM stk_track_ticks WHERE occ=? ORDER BY at DESC LIMIT 1",
                               (leg["occ"],)).fetchone()
        if row is None or not row["quoted_at"]:
            return None
        received, quoted = datetime.fromisoformat(row["at"]), datetime.fromisoformat(row["quoted_at"])
        values = (row["bid"], row["ask"])
        if (received < after or quoted < after or received > now or quoted > now or now - quoted > FRESHNESS
                or any(v is None or not math.isfinite(v) or v <= 0 for v in values) or row["ask"] < row["bid"]):
            return None
        quotes.append(dict(row))
    return quotes


def advance(market: sqlite3.Connection, research: sqlite3.Connection, now: datetime) -> int:
    """Bounded rotating maintenance. The shared tracker supplies quotes; this makes no provider calls."""
    # Serialize cancellation and entry decisions. A cancellation cannot race an already-read queued ticket.
    with transaction(research):
        return _advance(market, research, now)


def _advance(market: sqlite3.Connection, research: sqlite3.Connection, now: datetime) -> int:
    cursor = get_state(research, "personal_cursor").get("id", "")
    sql = ("SELECT p.* FROM stk_play_selections p WHERE p.id>? AND NOT EXISTS (SELECT 1 FROM "
           "stk_play_selection_events e WHERE e.selection_id=p.id AND e.kind IN ('CLOSE','CANCEL','EXPIRED_NO_ENTRY')) "
           "ORDER BY p.id LIMIT 100")
    rows = research.execute(sql, (cursor,)).fetchall() or research.execute(sql, ("",)).fetchall()
    changed = 0
    for row in rows:
        current = ticket(research, row["id"], now)
        play = current["idea"]["play"]
        legs = play["legs"]
        if current["expired"]:
            if current["entry"] is None:
                _save(research, row["id"], "EXPIRED_NO_ENTRY", now, {"reason": "Expired before a fresh entry quote"})
                changed += 1
                continue
            day = settle_day(date.fromisoformat(play["expiration"]))
            close = market.execute("SELECT close FROM stk_bars WHERE symbol=? AND day=?",
                                   (play["symbol"], day_of(day))).fetchone()
            if close is None:
                close = research.execute("SELECT close FROM stk_track_closes WHERE symbol=? AND session=?",
                                         (play["symbol"], day_of(day))).fetchone()
            if close is not None:
                _save(research, row["id"], "CLOSE", cal.session_close(day),
                      {"kind": "expiry_comparison", "value": settlement(legs, close[0]), "underlying": close[0],
                       "received_at": to_iso(now), "note": "Intrinsic expiry comparison; no exercise or assignment"})
                changed += 1
            continue
        if not (cal.is_trading_day(cal.new_york(now).date()) and
                time(9, 30) <= cal.new_york(now).time() < time(16)):
            continue
        entry = current["entry"]
        requested = _event(research, row["id"], "CLOSE_REQUEST")
        after = datetime.fromisoformat(requested["at"] if requested else row["eligible_at"])
        if entry and not requested:
            continue
        quotes = _quotes(research, legs, after, now)
        if quotes is None:
            continue
        if entry is None:
            net = sum(q["ask"] if leg["side"] == "buy" else -q["bid"]
                      for leg, q in zip(legs, quotes, strict=True))
            price = net if play["action"] == "BUY" else -net
            if price <= 0 or basis_loss(play["action"], price, legs) <= 0:
                continue
            _save(research, row["id"], "ENTRY", now, {"price": price, "quotes": quotes,
                "note": "Fresh quote-based PAPER reference; no brokerage order or liquidity guarantee"})
        else:
            value = sum(q["bid"] if leg["side"] == "buy" else -q["ask"]
                        for leg, q in zip(legs, quotes, strict=True))
            _save(research, row["id"], "CLOSE", now, {"value": value, "quotes": quotes, "kind": "quoted"})
        changed += 1
    if rows:
        put_state(research, "personal_cursor", {"id": rows[-1]["id"]}, now)
    return changed


def raw_path(research: sqlite3.Connection, legs: list[dict[str, Any]], start: datetime,
             end: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    daily_by: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    intraday_by: dict[int, dict[str, list[tuple[datetime, float, float, float | None]]]] = defaultdict(
        lambda: defaultdict(list))
    states: dict[int, dict[str, str]] = defaultdict(dict)
    for leg in legs:
        for row in research.execute("SELECT * FROM stk_track_days WHERE occ=? AND session>=? ORDER BY session",
                                    (leg["occ"], day_of(cal.new_york(start).date()))):
            daily_by[row["session"]][leg["occ"]] = dict(row)
        for row in research.execute("SELECT * FROM stk_track_intraday WHERE occ=? AND session>=? ORDER BY session",
                                    (leg["occ"], day_of(cal.new_york(start).date()))):
            states[row["session"]][leg["occ"]] = row["status"]
            for minute, bid, ask, underlying, _ in unpack(row["packed"]):
                if minute is None or bid is None or ask is None:
                    continue
                stamp = datetime.combine(date.fromordinal(row["session"]), time(int(minute)//60, int(minute)%60),
                                         cal.NEW_YORK)
                intraday_by[row["session"]][leg["occ"]].append((stamp, bid, ask, underlying))
        for row in research.execute("SELECT * FROM stk_track_ticks WHERE occ=? ORDER BY at", (leg["occ"],)):
            if row["bid"] is not None and row["ask"] is not None:
                intraday_by[row["session"]][leg["occ"]].append((datetime.fromisoformat(row["at"]), row["bid"],
                                                              row["ask"], row["underlying"]))
                states[row["session"]].setdefault(leg["occ"], "live")
    daily, intraday = [], []
    for day, quotes in sorted(daily_by.items()):
        if len(quotes) < len(legs):
            continue
        rows = [quotes[leg["occ"]] for leg in legs]
        if any(q["bid"] is None or q["ask"] is None for q in rows):
            continue
        value = sum(q["bid"] if leg["side"] == "buy" else -q["ask"]
                    for leg, q in zip(legs, rows, strict=True))
        status = next((s for s in ("estimated", "modelled") if any(q["status"] == s for q in rows)),
                      "settled" if all(q["status"] == "settled" for q in rows) else "quoted")
        at = cal.session_close(date.fromordinal(day))
        if start <= at <= end:
            daily.append({"t": date.fromordinal(day).isoformat(), "at": to_iso(at), "value": value, "status": status,
                          "underlying": rows[0]["underlying"], "note": rows[0]["note"]})
    for day, paths in sorted(intraday_by.items()):
        for at, value, underlying, status in _combine(legs, paths, states[day]):
            if start <= at <= end:
                intraday.append({"t": to_iso(at), "at": to_iso(at), "value": value, "status": status,
                                 "underlying": underlying, "note": None})
    return daily, intraday


def view(research: sqlite3.Connection, ident: str, now: datetime, *, personal: bool = False) -> dict[str, Any]:
    current = ticket(research, ident, now) if personal else None
    found = current["idea"] if current else idea(research, ident)
    play, entry = found["play"], current["entry"] if current else None
    prepared = plan(research, found["id"], now)
    if current and entry is None:
        return {"idea": found, "tracking": current, "plan": prepared, "chart": None}
    legs, action = play["legs"], play["action"]
    start = datetime.fromisoformat(entry["at"] if entry else found["identified_at"])
    price = entry["price"] if entry else play["entry"]
    expiry = settle_day(date.fromisoformat(play["expiration"]))
    closed = current["exit"] if current else None
    end = min(now, cal.session_close(expiry), datetime.fromisoformat(closed["at"]) if closed else now)
    quarantine = None
    if not personal:
        from desk.stocks.play_archive import ARCHIVE_CATEGORY

        row = research.execute("SELECT reason FROM stk_play_quarantine WHERE session=? AND category=? AND rank=1 "
                               "AND twin=0", (found["session"],ARCHIVE_CATEGORY+found["id"])).fetchone()
        if row:
            quarantine = row[0]
            split = re.search(r"Split on (\d{4}-\d{2}-\d{2})",quarantine)
            if split:
                end = min(end,cal.session_close(cal.previous_trading_day(date.fromisoformat(split[1]))))
    daily, intraday = raw_path(research, legs, start, end)
    if not personal:
        from desk.stocks.play_archive import ARCHIVE_CATEGORY

        category = ARCHIVE_CATEGORY+found["id"]
        extra = research.execute("SELECT day,status,value,underlying,note FROM stk_play_marks "
                                 "WHERE session=? AND category=? AND rank=1 AND twin=0 ORDER BY day",
                                 (found["session"],category)).fetchall()
        extra += research.execute("SELECT day,'provisional' AS status,value,underlying,note "
                                  "FROM stk_play_marks_provisional WHERE session=? AND category=? AND rank=1 AND twin=0",
                                  (found["session"],category)).fetchall()
        present = {p["t"] for p in daily}
        for row in extra:
            day = date.fromordinal(row["day"])
            stamp = cal.session_close(day)
            if start <= stamp <= end and day.isoformat() not in present:
                daily.append({"t":day.isoformat(),"at":to_iso(stamp),"value":row["value"],"status":row["status"],
                              "underlying":row["underlying"],"note":row["note"]})
                present.add(day.isoformat())
        daily.sort(key=lambda p:p["at"])
        # Preserve the real saved starting quote even if later tracking was interrupted.
        if (start <= end and all(isinstance(leg.get(k),(int,float)) and math.isfinite(leg[k])
                                for leg in legs for k in ("bid","ask"))
                and all(0 <= leg["bid"] <= leg["ask"] and leg["ask"] > 0 for leg in legs)):
            initial = sum(leg["bid"] if leg["side"] == "buy" else -leg["ask"] for leg in legs)
            intraday.insert(0,{"t":to_iso(start),"at":to_iso(start),"value":initial,"status":"quoted",
                               "underlying":play.get("underlying_price"),"note":"Original saved identification quote"})
    if entry:
        initial = sum(q["bid"] if leg["side"] == "buy" else -q["ask"]
                      for leg, q in zip(legs, entry["quotes"], strict=True))
        intraday.insert(0, {"t": entry["at"], "at": entry["at"], "value": initial, "status": "quoted",
                            "underlying": entry["quotes"][0]["underlying"], "note": "PAPER entry quote"})
    if closed:
        daily.append({"t": closed["at"], "at": closed["at"], "value": closed["value"],
                      "status": "settled" if closed["kind"] == "expiry_comparison" else "quoted",
                      "underlying": closed.get("underlying"), "note": closed.get("note")})
    for point in [*daily, *intraday]:
        point["close"] = pnl(action, price, legs, point["value"], point["status"] == "settled")
    latest = {}
    for leg in legs:
        row = research.execute("SELECT * FROM stk_track_days WHERE occ=? ORDER BY session DESC LIMIT 1",
                               (leg["occ"],)).fetchone()
        if row is not None:
            latest[leg["occ"]] = dict(row)
    chart = {"play": play, "label": label(play), "legs": legs, "bases": {"close": price}, "first_seen": None,
             "basis_labels": {"close": "My PAPER entry" if personal else "Idea reference"}, "daily": daily,
             "intraday": intraday, "exits": exit_opportunities(daily, intraday, {"close": start}, end),
             "contract_quantity": current["quantity"] if current else 1, "expired": cal.session_close(expiry) <= now,
             "expiration": play["expiration"], "settles": expiry.isoformat(), "twin": None,"quarantine":quarantine,
             "greeks": {"then": {g: play.get(g) for g in ("iv", "delta", "gamma", "theta", "vega")},
                        "now": _net(legs, latest), "schwab_now": _net(legs, latest, "schwab_")},
             "intraday_note": "The quote path reflects stored observations; intraday detail is retained for 30+ days. "
                              "Personal references are separate from automatic Plays grading and Learning."}
    return {"idea": found, "tracking": current, "plan": prepared, "chart": chart}


def listing(research: sqlite3.Connection, now: datetime, offset: int = 0, limit: int = 20) -> dict[str, Any]:
    rows = research.execute("SELECT id FROM stk_play_selections ORDER BY confirmed_at DESC,id LIMIT ? OFFSET ?",
                            (limit, offset)).fetchall()
    return {"rows": [ticket(research, row[0], now) for row in rows],
            "total": research.execute("SELECT COUNT(*) FROM stk_play_selections").fetchone()[0], "offset": offset}
