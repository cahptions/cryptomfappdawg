"""Compare a read-only follow-up with one ordinary rotation slot."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta
from typing import Any

from desk.core.clock import to_iso
from desk.core.hashing import canonical_json, sha256_hex
from desk.stocks import calendar as cal
from desk.stocks import execution, learning
from desk.stocks.learning_decision import read
from desk.stocks.paper.fills import OPTION_FEE_PER_CONTRACT
from desk.stocks.repo import StocksMarket

# Candidate choice must be frozen before consulting the provider.
VERSION = "cash-boundary-v1"
MAX_AGE = timedelta(minutes=15)


def assign(market: StocksMarket, research: sqlite3.Connection, ledger: sqlite3.Connection, now: datetime,
           wanted: list[str], protected: set[str], source: str) -> tuple[date, str, str] | None:
    local = cal.new_york(now)
    day = local.date()
    if not (cal.is_trading_day(day) and local.time() >= time(9, 30) and local < cal.session_close(day)):
        return None
    conn = market.conn
    previous = conn.execute("SELECT 1 FROM stk_research_quote_trials WHERE session=?", (day.toordinal(),)).fetchone()
    if previous:
        return None
    for account in reversed(learning.SMALL_ACCOUNTS):
        state = learning.state(ledger, "retail:" + account, {})
        decision_id = state.get("decision_id")
        if not decision_id:
            continue
        decision = read(ledger, account, decision_id)
        if not decision or decision["gate"] != "cash" or not decision["symbol"] or not decision["source"]:
            continue
        if not timedelta(0) <= now - datetime.fromisoformat(decision["at"]) <= MAX_AGE:
            continue
        legs = [leg["occ"] for leg in decision["source"]["legs"]]
        if not legs or decision["structure"] not in {"long_call", "long_put", "long_straddle"}:
            continue
        target, cash, needed = decision["symbol"], decision["cash"], decision["one_unit"]
        if not cash or not cash < needed <= cash * 1.15 or target in wanted or target in protected:
            continue
        target_price = research.execute(
            "SELECT json_extract(detail_json,'$.price.close'),json_extract(detail_json,'$.price.dollar_volume_20d') "
            "FROM stk_scores_latest WHERE symbol=?", (target,),
        ).fetchone()
        if not target_price or not target_price[0] or not target_price[1]:
            continue
        rotation = None
        for symbol in reversed(wanted):
            if symbol in protected:
                continue
            features = research.execute(
                "SELECT json_extract(detail_json,'$.price.close'),json_extract(detail_json,'$.price.dollar_volume_20d') "
                "FROM stk_scores_latest WHERE symbol=?", (symbol,),
            ).fetchone()
            if (features and features[0] and features[1] and .5 <= features[0] / target_price[0] <= 2
                    and .25 <= features[1] / target_price[1] <= 4):
                rotation = symbol
                break
        if rotation is None:
            continue
        arm = "follow_up" if int(sha256_hex(f"{VERSION}:{day}".encode())[-1], 16) % 2 else "rotation"
        written = conn.execute(
            "INSERT OR IGNORE INTO stk_research_quote_trials "
            "(session,started_at,assigned,follow_up,rotation,source,decision_id,account,question_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (day.toordinal(), to_iso(now), arm, target, rotation, source, decision_id, account,
             canonical_json({"legs": legs, "cash": cash, "one_unit": needed, "decision_at": decision["at"]})),
        ).rowcount
        if written:
            return day, rotation, target if arm == "follow_up" else rotation
    return None


def cash_result(market: StocksMarket, planned: sqlite3.Row, day: date, now: datetime) -> dict[str, Any] | None:
    if planned["assigned"] != "follow_up" or not planned["question_json"]:
        return None
    question = json.loads(planned["question_json"])
    legs = [(occ, 1) for occ in question["legs"]]
    result = {**question, "status": "unknown", "reason": "No synchronized receipt for the original legs"}
    snapshot = market.chains_on(day, [planned["follow_up"]], observed_by=now).get(planned["follow_up"], {})
    if snapshot.get("source") != planned["source"]:
        return result
    for expiry, contracts in snapshot.get("expirations", {}).items():
        at = snapshot.get("receipt_times", {}).get(expiry)
        if not at or at < planned["started_at"]:
            continue
        quotes = {q.occ: execution.Quote.of(q) for q in contracts}
        if not all(occ in quotes for occ, _ in legs):
            continue
        receipt = datetime.fromisoformat(at)
        checked = execution.evidence(legs, quotes, receipt, receipt)
        if checked["status"] != "supported":
            return {**result, "reason": checked["why"]}
        if checked["quoted_at"] <= question.get("decision_at", planned["started_at"]):
            return {**result, "reason": "At least one leg was quoted before the original decision"}
        cost = round(checked["net_debit"] + len(legs) * OPTION_FEE_PER_CONTRACT, 2)
        return {**result, "status": "fits" if cost <= question["cash"] else "exceeds cash",
                "reason": None, "cost": cost, "quoted_at": checked["quoted_at"], "received_at": at}
    return result


def finish(market: StocksMarket, day: date, symbol: str, now: datetime, detail: dict[str, Any]) -> None:
    scheduled = market.conn.execute(
        "SELECT * FROM stk_research_quote_trials WHERE session=? AND finished_at IS NULL", (day.toordinal(),),
    ).fetchone()
    if scheduled is None or scheduled[scheduled["assigned"]] != symbol:
        return
    data = detail.get("coverage", {}).get(symbol, {})
    count = data.get("liquid_contracts", 0)
    stored = market.conn.execute(
        "SELECT COALESCE(SUM(length(chain_zlib)),0) FROM stk_option_chains c "
        "JOIN stk_research_quote_trials s ON s.session=c.day WHERE c.underlying=? AND s.session=? "
        "AND c.observed_at BETWEEN s.started_at AND ?", (symbol, day.toordinal(), to_iso(now)),
    ).fetchone()[0]
    outcome = {
        "symbol": symbol, "retained_bytes": stored, "seconds": detail.get("symbol_seconds", {}).get(symbol, 0.0),
        "provider_calls": detail.get("provider_calls", {}).get(symbol, 0),
        "captured_contracts": count, "usable": bool(count >= 4 and data.get("session") == day.isoformat()),
        "status": data.get("status", "provider failure" if symbol in detail.get("errors", {}) else "no receipt"),
        "affordability": cash_result(market, scheduled, day, now),
    }
    market.conn.execute(
        "UPDATE stk_research_quote_trials SET finished_at=?,result_json=? WHERE session=? AND finished_at IS NULL",
        (to_iso(now), canonical_json(outcome), day.toordinal()),
    )


def summary(conn: sqlite3.Connection, now: datetime, *, enabled: bool) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM stk_research_quote_trials ORDER BY session DESC LIMIT 60").fetchall()
    arms: dict[str, dict[str, Any]] = {
        arm: {"assigned": 0, "usable": 0, "unknown": 0, "provider_calls": 0, "seconds": 0.0, "retained_bytes": 0}
        for arm in ("follow_up", "rotation")
    }
    latest = None
    questions = {"fits": 0, "exceeds cash": 0, "unknown": 0}
    for row in rows:
        result = json.loads(row["result_json"]) if row["result_json"] else None
        counts = arms[row["assigned"]]
        counts["assigned"] += 1
        if result:
            counts["usable"] += int(result["usable"])
            counts["provider_calls"] += result["provider_calls"]
            counts["seconds"] += result.get("seconds", 0.0)
            counts["retained_bytes"] += result.get("retained_bytes", 0)
            if result.get("affordability"):
                questions[result["affordability"]["status"]] += 1
        elif now - datetime.fromisoformat(row["started_at"]) > MAX_AGE:
            counts["unknown"] += 1
        if latest is None:
            latest = {"session": date.fromordinal(row["session"]).isoformat(), "assigned": row["assigned"],
                      "account": row["account"], "decision_id": row["decision_id"],
                      "follow_up": row["follow_up"], "rotation": row["rotation"], "result": result}
    return {"enabled": enabled, "arms": arms, "latest": latest, "window": 60, "affordability": questions}
