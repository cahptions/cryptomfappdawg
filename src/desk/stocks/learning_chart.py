"""Read-only retained Learning history and dated ledger context. Never manufactures marks or trading events."""

from __future__ import annotations

import math
import sqlite3
from collections import deque
from datetime import datetime, time, timedelta
from typing import Any

from desk.core.clock import to_iso
from desk.stocks import calendar as cal
from desk.stocks.learning import ACCOUNTS
from desk.stocks.paper.book import MICRO

RANGES = ("1D", "1W", "1M", "3M", "YTD", "1Y", "Max")
MAX_POINTS = 1200
MAX_EVENTS = 160


def history(conn: sqlite3.Connection, account: str, window: str = "Max") -> dict[str, Any]:
    available = conn.execute(
        "SELECT MIN(at) first,MAX(at) last,COUNT(*) n FROM stk_learning_nav WHERE account=?", (account,)
    ).fetchone()
    result: dict[str, Any] = {
        "account": account,
        "range": window,
        "points": [],
        "events": [],
        "available_first": available["first"],
        "available_last": available["last"],
        "available_observations": available["n"],
        "max_points": MAX_POINTS,
        "max_events": MAX_EVENTS,
        "basis": "Recorded PAPER marks; virtual funding is excluded from trading P/L.",
        "source": "Learning ledger; fills and marks require timestamped option quotes.",
    }
    if not available["last"]:
        return {**result, "observations": 0, "event_count": 0, "sampled": False}
    last = cal.new_york(datetime.fromisoformat(available["last"]))
    first = (
        last.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        if window == "YTD"
        else (
            datetime.combine(last.date(), time(), cal.NEW_YORK)
            if window == "1D"
            else last - timedelta(days={"1W": 7, "1M": 30, "3M": 91, "1Y": 365}.get(window, 0))
        )
    )
    begin = max(available["first"], to_iso(first)) if window != "Max" else available["first"]
    count = conn.execute(
        "SELECT COUNT(*) FROM stk_learning_nav WHERE account=? AND at>=? AND at<=?", (account, begin, available["last"])
    ).fetchone()[0]
    size = max(1, math.ceil(count / (MAX_POINTS // 6)))
    points: list[dict[str, Any]] = []
    batch: list[dict[str, Any]] = []

    def flush() -> None:
        if not batch:
            return
        chosen = {
            r["at"]: r
            for r in [
                batch[0],
                batch[-1],
                min(batch, key=lambda r: r["pnl"]),
                max(batch, key=lambda r: r["pnl"]),
                min(batch, key=lambda r: r["equity"]),
                max(batch, key=lambda r: r["equity"]),
            ]
        }
        points.extend(chosen[t] for t in sorted(chosen))
        batch.clear()

    for row in conn.execute(
        "SELECT at,equity,contributed,pnl FROM stk_learning_nav WHERE account=? AND at>=? AND at<=? ORDER BY at",
        (account, begin, available["last"]),
    ):
        batch.append(dict(row))
        if len(batch) >= size:
            flush()
    flush()
    # Merge the dated cash/position ledgers once; a historical tooltip never substitutes today's balance.
    cash_rows = iter(
        conn.execute(
            "SELECT at,amount_micros FROM stk_cash WHERE account=? AND at<=? ORDER BY at,id", (account, available["last"])
        )
    )
    cash_row = next(cash_rows, None)
    cash = 0
    # SQL streams the complete account timeline. Marker payloads stay bounded even after years of experiments.
    activity = iter(
        conn.execute(
            "SELECT opened_at at,1 delta,0 pnl,entry_fees_micros fees FROM stk_positions WHERE account=? AND opened_at<=? "
            "UNION ALL SELECT closed_at,-1,exit_credit_micros-exit_fees_micros-entry_cost_micros-entry_fees_micros,"
            "exit_fees_micros FROM stk_positions WHERE account=? AND closed_at<=? ORDER BY at",
            (account, available["last"], account, available["last"]),
        )
    )
    action = next(activity, None)
    event_sql = (
        "SELECT opened_at at,'entry' kind,id position_id,symbol,structure,0 amount,0 pnl FROM stk_positions "
        "WHERE account=? AND opened_at>=? AND opened_at<=? UNION ALL "
        "SELECT closed_at,'exit',id,symbol,structure,0,"
        "exit_credit_micros-exit_fees_micros-entry_cost_micros-entry_fees_micros FROM stk_positions "
        "WHERE account=? AND closed_at>=? AND closed_at<=? UNION ALL "
        "SELECT at,'funding',NULL,NULL,NULL,amount_micros,0 FROM stk_cash "
        "WHERE account=? AND reason='DEPOSIT' AND at>=? AND at<=?"
    )
    args = (account, begin, available["last"]) * 3
    event_count = conn.execute("SELECT COUNT(*) FROM (" + event_sql + ")", args).fetchone()[0]  # noqa: S608 — static SQL
    events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
    for row in conn.execute(event_sql + " ORDER BY at DESC,kind,position_id LIMIT ?", (*args, MAX_EVENTS)):
        event = dict(row)
        event["amount"] /= MICRO
        event["pnl"] /= MICRO
        event["label"] = (
            f"Virtual funding +${event['amount']:,.2f}"
            if event["kind"] == "funding"
            else f"Closed {row['symbol']} · ${event['pnl']:,.2f} net"
            if event["kind"] == "exit"
            else f"Opened {row['symbol']} {row['structure'].replace('_', ' ')}"
        )
        events.appendleft(event)
    open_positions, closed_trades, realized, fees = 0, 0, 0.0, 0.0
    peak = max(
        0.0,
        conn.execute("SELECT COALESCE(MAX(pnl),0) FROM stk_learning_nav WHERE account=? AND at<?", (account, begin)).fetchone()[
            0
        ],
    )
    for point in points:
        while cash_row is not None and cash_row["at"] <= point["at"]:
            cash += cash_row["amount_micros"]
            cash_row = next(cash_rows, None)
        while action is not None and action["at"] <= point["at"]:
            open_positions += action["delta"]
            closed_trades += action["delta"] < 0
            realized += action["pnl"] / MICRO
            fees += action["fees"] / MICRO
            action = next(activity, None)
        peak = max(peak, point["pnl"])
        point.update(
            cash=cash / MICRO,
            realized_pnl=realized,
            unrealized_pnl=round(point["pnl"] - realized, 6),
            fees=round(fees, 6),
            drawdown=point["pnl"] - peak,
            open_positions=open_positions,
            closed_trades=closed_trades,
        )
    return {
        **result,
        "points": points,
        "events": list(events),
        "event_count": event_count,
        "observations": count,
        "sampled": count > len(points),
        "range_first": points[0]["at"] if points else None,
        "range_last": points[-1]["at"] if points else None,
        "sampling": "Recorded first/last and P/L/equity extrema per bucket; intermediate observations omitted"
        if count > len(points)
        else "Every recorded observation in this range",
        "title": next((title for ident, title, _ in ACCOUNTS if ident == account), account),
    }
