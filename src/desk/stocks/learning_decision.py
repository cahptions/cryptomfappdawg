"""Read a finite PAPER account's original decision and its later order and cash effects."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from desk.stocks import learning
from desk.stocks.paper.book import usd

NEXT = {
    "quotes": "A qualifying option quote within the freshness window lets PAPER evaluate an entry.",
    "policy": "A research family must pass the positive-return gate before PAPER can consider an entry.",
    "cash": "One qualifying whole unit, including modeled entry fees, must fit the book's cash at a later decision.",
    "eligibility": "A new quote or a change in open positions or entry spacing can make a contract eligible.",
    "entry": "A later qualifying quote for every leg, enough capacity and enough cash are required to fill.",
}


def latest_id(conn: sqlite3.Connection, account: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM stk_decisions WHERE account=? AND action IN ('ENTER','SKIP') "
        "ORDER BY asof DESC,id DESC LIMIT 1", (account,),
    ).fetchone()
    return int(row["id"]) if row else None


def read(conn: sqlite3.Connection, account: str, decision_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM stk_decisions WHERE id=? AND account=? AND action IN ('ENTER','SKIP')", (decision_id, account),
    ).fetchone()
    if row is None:
        return None
    evidence = json.loads(row["evidence_json"])
    context = evidence.get("retail_context") or {}
    plan = evidence.get("plan") or {}
    prediction = context.get("prediction") or evidence.get("prediction") or plan.get("transfer_policy") or {}
    observation = context.get("observation") or {}
    source_key = observation.get("source_key") or plan.get("source_key")
    source = conn.execute(
        "SELECT observed_at,underlying FROM stk_learning_sources WHERE source_key=? AND observed_at<=?",
        (source_key, row["at"]),
    ).fetchone() if source_key else None
    # Legacy orders saved the decision cash in the forecast. The live account balance is irrelevant here.
    captured = context.get("cash", prediction.get("decision_cash"))
    gate = context.get("gate") or ("entry" if row["action"] == "ENTER" else None)
    orders = []
    for order in conn.execute(
        "SELECT id,state,created_at,resolved_at,note,order_json FROM stk_pending WHERE account=? AND decision_id=? ORDER BY id",
        (account, decision_id),
    ):
        orders.append({"id": order["id"], "state": order["state"], "created_at": order["created_at"],
                       "resolved_at": order["resolved_at"], "note": order["note"],
                       "requested_units": json.loads(order["order_json"]).get("desired_units")})
    fills = []
    for pos in conn.execute(
        "SELECT p.id,p.state,p.opened_at,p.closed_at,p.exit_reason,p.entry_cost_micros,p.entry_fees_micros,"
        "p.exit_credit_micros,p.exit_fees_micros,f.units,f.order_id "
        "FROM stk_positions p LEFT JOIN stk_learning_fills f ON f.position_id=p.id "
        "WHERE p.account=? AND p.decision_id=? ORDER BY p.opened_at,p.id", (account, decision_id),
    ):
        paid = pos["entry_cost_micros"] + pos["entry_fees_micros"]
        net = usd(pos["exit_credit_micros"] - pos["exit_fees_micros"] - paid) if (
            pos["exit_credit_micros"] is not None and pos["exit_fees_micros"] is not None) else None
        fills.append({"position_id": pos["id"], "order_id": pos["order_id"], "units": pos["units"],
                      "opened_at": pos["opened_at"], "closed_at": pos["closed_at"], "exit_reason": pos["exit_reason"],
                      "state": pos["state"], "entry_debit": usd(paid), "pnl": net})
    return {
        "id": decision_id, "account": account, "title": next(title for key, title, _ in learning.ACCOUNTS if key == account),
        "at": row["at"], "session": date.fromordinal(row["asof"]).isoformat(), "action": row["action"],
        "symbol": row["symbol"] or None, "reason": row["reasons"],
        "next_condition": NEXT.get(gate) if isinstance(gate, str) else None,
        "gate": gate, "cash": captured, "equity": context.get("equity", prediction.get("decision_equity")),
        "pending_before_decision": context.get("pending_orders"),
        "target_budget": context.get("target_budget", prediction.get("target_budget")),
        "one_unit": context.get("one_unit"), "structure": context.get("structure") or plan.get("structure"),
        "snapshot_count": context.get("snapshot_count"),
        "policy": {key: prediction[key] for key in ("mode", "policy_hash", "blocks", "days", "expected_net_return",
                   "matured_through", "exit_days") if key in prediction} | {"mode": context.get("mode", prediction.get("mode"))},
        "source": {**dict(source), "id": source_key, "legs": observation.get("legs", []),
                   "last_quote_at": observation.get("last_quote_at")} if source else None,
        "orders": orders, "fills": fills,
    }


def books(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Current finite-book balances and the latest linkable decision for each account."""
    out = []
    for ident, title, _ in learning.ACCOUNTS:
        if ident not in learning.SMALL_ACCOUNTS:
            continue
        cash, funding, count = conn.execute(
            "SELECT COALESCE(SUM(amount_micros),0),"
            "COALESCE(SUM(CASE WHEN reason='DEPOSIT' THEN amount_micros END),0),COUNT(*) FROM stk_cash WHERE account=?",
            (ident,),
        ).fetchone()
        marks, open_positions = conn.execute(
            "SELECT COALESCE(SUM(last_value_micros),0),COUNT(*) FROM stk_positions WHERE account=? AND state='OPEN'",
            (ident,),
        ).fetchone()
        pending = conn.execute("SELECT COUNT(*) FROM stk_pending WHERE account=? AND state='PENDING'", (ident,)).fetchone()[0]
        policy = learning.state(conn, "retail:" + ident, {})
        out.append({"id": ident, "title": title, "phase": policy.get("phase", "Waiting for a decision"),
                    "decision_id": policy.get("decision_id") or latest_id(conn, ident),
                    "cash": usd(cash) if count else None, "equity": usd(cash + marks) if count else None,
                    "trading_pnl": usd(cash + marks - funding) if count else None,
                    "open_positions": open_positions, "pending": pending})
    return out
