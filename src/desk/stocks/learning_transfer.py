"""Read-only comparison of frozen small-book selection evidence with subsequent PAPER fills."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime
from statistics import fmean
from typing import Any

from desk.stocks.learning import SMALL_ACCOUNTS
from desk.stocks.paper.book import MICRO

ORDER_LIMIT = 120

# Each state has its own (account,state,id) index range. The extra row tells us if the
# oldest decision session was cut by the limit. Single statement = one SQLite snapshot.
_ORDERS = """
    WITH ids AS (
        SELECT id FROM (SELECT id FROM stk_pending WHERE account=:account AND state='PENDING' ORDER BY id DESC LIMIT :limit)
        UNION ALL
        SELECT id FROM (SELECT id FROM stk_pending WHERE account=:account AND state='FILLED' ORDER BY id DESC LIMIT :limit)
        UNION ALL
        SELECT id FROM (SELECT id FROM stk_pending WHERE account=:account AND state='CANCELLED' ORDER BY id DESC LIMIT :limit)
    ),
    recent AS (SELECT id FROM ids ORDER BY id DESC LIMIT :limit)
    SELECT o.id, o.state, o.order_json, o.note, d.at, d.asof, d.symbol,
           COUNT(f.position_id) AS fills, COUNT(p.id) AS linked, COALESCE(SUM(f.units), 0) AS units,
           COUNT(CASE WHEN p.state='OPEN' THEN 1 END) AS still_open,
           COUNT(CASE WHEN p.state='CLOSED' AND p.exit_fees_micros IS NOT NULL
                      AND p.exit_credit_micros IS NOT NULL AND p.exit_day IS NOT NULL THEN 1 END) AS closed,
           MAX(p.exit_day) AS last_exit,
           SUM(p.entry_cost_micros + p.entry_fees_micros) AS paid,
           SUM(p.exit_credit_micros - p.exit_fees_micros) AS returned
    FROM recent JOIN stk_pending o ON o.id=recent.id JOIN stk_decisions d ON d.id=o.decision_id
    LEFT JOIN stk_learning_fills f ON f.order_id=o.id
    LEFT JOIN stk_positions p ON p.id=f.position_id AND p.account=o.account AND p.decision_id=d.id
    GROUP BY o.id ORDER BY o.id DESC
"""


def _prior_estimate(policy: dict[str, Any], at: str) -> float | None:
    """Only a finite estimate supported by research already mature at the documented selection time."""
    estimate, blocks = policy.get("expected_net_return"), policy.get("blocks")
    if (not isinstance(estimate, (int, float)) or isinstance(estimate, bool) or not math.isfinite(estimate)
            or type(blocks) is not int or blocks <= 0
            or policy.get("mode") not in ("provisional", "research_champion")):
        return None
    try:
        cutoff, asof, decision = (datetime.fromisoformat(v) for v in (policy["matured_through"], policy["asof"], at))
        if cutoff.tzinfo and asof.tzinfo and decision.tzinfo and cutoff < asof <= decision:
            return float(estimate)
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _receipt(row: sqlite3.Row) -> dict[str, Any]:
    plan = json.loads(row["order_json"])
    policy = plan.get("transfer_policy") or {}
    prior = _prior_estimate(policy, row["at"])
    cost = row["paid"] or 0
    if row["state"] == "PENDING":
        state = "waiting"
    elif row["state"] == "CANCELLED" and not row["fills"]:
        state = "unfilled"
    elif not row["fills"] or row["linked"] != row["fills"] or cost <= 0:
        state = "unavailable"
    elif row["still_open"]:
        state = "open"
    else:
        state = "closed" if row["closed"] == row["fills"] else "unavailable"
    realized = row["returned"] - cost if state == "closed" else None
    horizon = plan.get("hold_minutes")
    same_horizon = horizon is not None and horizon == plan.get("recipe_hold_minutes", horizon)
    return {
        "order_id": row["id"], "decision_at": row["at"], "session": date.fromordinal(row["asof"]).isoformat(),
        "symbol": row["symbol"], "structure": plan.get("structure"), "bucket": plan.get("bucket"),
        "hold_minutes": horizon, "mode": policy.get("mode"), "prior_blocks": policy.get("blocks"),
        "matured_through": policy.get("matured_through") if prior is not None else None,
        "research_estimate": prior, "same_horizon": same_horizon,
        "state": state, "order_state": row["state"], "order_note": row["note"],
        "requested_units": plan.get("desired_units"), "filled_units": row["units"], "fills": row["fills"],
        "entry_debit": cost / MICRO, "pnl": realized / MICRO if realized is not None else None,
        "net_return": realized / cost if realized is not None else None,
        "last_exit": date.fromordinal(row["last_exit"]).isoformat() if state == "closed" else None,
    }


def review(conn: sqlite3.Connection, account: str) -> dict[str, Any]:
    """Equal weight per underlying/decision session after pooling the filled debits within it.

    An open *comparable* sibling holds its group out of the paired means until it resolves.
    Orders with a changed holding policy still show the actual ledger outcome in their receipt.
    """
    if account not in SMALL_ACCOUNTS:
        raise ValueError("Transfer review requires a small Learning account")
    rows = conn.execute(_ORDERS, {"account": account, "limit": ORDER_LIMIT + 1}).fetchall()
    receipts = [_receipt(r) for r in rows[:ORDER_LIMIT]]
    boundary = date.fromordinal(rows[-1]["asof"]).isoformat() if len(rows) > ORDER_LIMIT else None
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in receipts:
        if (item["research_estimate"] is not None and item["same_horizon"]
                and item["state"] != "unfilled" and item["session"] != boundary):
            groups[item["session"], item["symbol"]].append(item)

    actual, estimated, intervals = [], [], []
    matched_orders = 0
    for (session, _symbol), items in groups.items():
        if any(item["net_return"] is None for item in items):
            continue
        # Weight both sides by the actual debit of each decision's fills within its group.
        paid = sum(item["entry_debit"] for item in items)
        actual.append(sum(item["pnl"] for item in items) / paid)
        estimated.append(sum(item["research_estimate"] * item["entry_debit"] for item in items) / paid)
        matched_orders += len(items)
        intervals.append((session, max(item["last_exit"] for item in items)))

    # Overlapping exposure, even across symbols, can reflect the same market move.
    # Counting distinct periods is descriptive and does not imply statistical independence.
    periods = 0
    end = ""
    for start, last_exit in sorted(intervals):
        if start > end:
            periods += 1
        end = max(end, last_exit)
    return {
        "account": account, "order_limit": ORDER_LIMIT, "truncated": bool(boundary), "orders_reviewed": len(receipts),
        "states": dict(Counter(item["state"] for item in receipts)), "matched_orders": matched_orders,
        "matched_groups": len(actual), "unresolved_groups": len(groups) - len(actual),
        "exposure_periods": periods, "research_mean": fmean(estimated) if estimated else None,
        "realized_mean": fmean(actual) if actual else None, "receipts": receipts,
    }
