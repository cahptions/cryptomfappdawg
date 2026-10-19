"""``desk paper-report``: learning-book results by profile/strategy/exit, reconciliation, and the $100 replica."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from desk.core.clock import parse_iso8601
from desk.core.config import LoadedConfig
from desk.core.units import sol_to_lamports
from desk.paper.engine import heartbeat_age, stop_file_set
from desk.paper.ledger import PaperLedger
from desk.paper.model import floor_int
from desk.storage.databases import Databases

LAMPORTS = Decimal(10 ** 9)


def _sol(lamports: int) -> str:
    return f"{Decimal(lamports) / LAMPORTS:.6f}"


def replica(closed: list[dict[str, Any]], still_open: list[dict[str, Any]], loaded: LoadedConfig,
            base_lamports: int) -> dict[str, Any]:
    """Estimate what the real $100 account would have done with the same signals (PLAN §13.1.1).

    Same entries in time order, but: cash = $100, at most ``limits.max_open_positions`` at once, ticket = fraction x
    min(equity, $100) (no evidence-gated growth yet), fee floor, daily loss limit, trades/day and cumulative-drawdown
    halt. Returns scale linearly from the learning-book fill; fixed fees are charged per trade unscaled. An estimate.
    """
    sizing, limits = loaded.settings.sizing, loaded.settings.limits
    events = sorted([*closed, *still_open], key=lambda p: (p["opened_at"], p["id"]))
    equity, peak = base_lamports, base_lamports
    active: list[tuple[str, int]] = []           # (closed_at or "", pnl)
    skipped: Counter[str] = Counter()
    taken, day_start, day, day_trades, day_realized, halted = 0, base_lamports, "", 0, 0, False
    max_drawdown = Decimal(0)
    for position in events:
        opened = position["opened_at"]
        for item in [a for a in active if a[0] and a[0] <= opened]:
            active.remove(item)
            equity += item[1]
            day_realized += item[1]
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, Decimal(peak - equity) / Decimal(peak))
        if opened[:10] != day:
            day, day_start, day_trades, day_realized = opened[:10], equity, 0, 0
        if halted or equity <= peak * (1 - limits.cumulative_drawdown_fraction):
            halted = True
            skipped["CUMULATIVE_DRAWDOWN_HALT"] += 1
            continue
        if day_realized <= -day_start * limits.daily_realized_loss_fraction:
            skipped["DAILY_LOSS_LIMIT"] += 1
            continue
        if day_trades >= limits.max_trades_per_day:
            skipped["TRADES_PER_DAY"] += 1
            continue
        if len(active) >= limits.max_open_positions:
            skipped["POSITION_LIMIT"] += 1
            continue
        ticket = floor_int(Decimal(min(equity, base_lamports)) * sizing.fixed_fraction)
        fixed = position["entry_fees_lamports"] + (position["exit_fees_lamports"] or 0)
        if ticket < sol_to_lamports(sizing.min_ticket_sol) or Decimal(fixed) > Decimal(ticket) * sizing.max_fixed_cost_fraction:
            skipped["FEE_FLOOR_OR_MINIMUM"] += 1
            continue
        taken += 1
        day_trades += 1
        if position.get("closed_at"):
            gross = Decimal(position["exit_lamports"] - position["ticket_lamports"]) / Decimal(position["ticket_lamports"])
            active.append((position["closed_at"], floor_int(gross * ticket) - fixed))
        else:
            active.append(("", 0))
    for closed_at, pnl in active:
        if closed_at:
            equity += pnl
    return {"start_lamports": base_lamports, "equity_lamports": equity, "equity_sol": _sol(equity),
            "return": str((Decimal(equity - base_lamports) / Decimal(base_lamports)).quantize(Decimal("0.0001"))),
            "trades_taken": taken, "skipped": dict(skipped), "max_drawdown": str(max_drawdown.quantize(Decimal("0.0001"))),
            "halted_by_cumulative_drawdown": halted,
            "note": "estimate from learning-book fills; open positions occupy slots at zero P&L"}


def paper_report(dbs: Databases, loaded: LoadedConfig, now: datetime) -> dict[str, Any]:
    ledger = PaperLedger(dbs.paper_ledger)
    if not ledger.initialized():
        return {"initialized": False, "note": "no PAPER book yet; start one with `desk paper`"}
    meta = ledger.meta()
    rows = [dict(r) for r in dbs.paper_ledger.execute("SELECT * FROM positions ORDER BY opened_at, id")]
    closed = [r for r in rows if r["state"] == "CLOSED"]
    still_open = [r for r in rows if r["state"] != "CLOSED"]
    profiles: dict[str, int] = defaultdict(int)
    by_strategy: dict[str, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl_lamports": 0, "returns": []})
    for row in closed:
        stored = json.loads(row["profiles_json"])
        for name in ("observed", "conservative", "stress"):
            profiles[name] += int(stored[name]["pnl_lamports"])
        bucket = by_strategy[row["strategy_id"]]
        bucket["trades"] += 1
        bucket["wins"] += row["realized_pnl_lamports"] > 0
        bucket["pnl_lamports"] += row["realized_pnl_lamports"]
        bucket["returns"].append(Decimal(row["realized_pnl_lamports"]) / Decimal(row["ticket_lamports"]))
    strategies = {name: {"trades": b["trades"], "win_rate": str(Decimal(b["wins"]) / b["trades"]),
                         "mean_return": str((sum(b["returns"], Decimal(0)) / b["trades"]).quantize(Decimal("0.0001"))),
                         "pnl_sol": _sol(b["pnl_lamports"])} for name, b in sorted(by_strategy.items())}
    unrealized = sum((r["last_mark_lamports"] or 0) - r["ticket_lamports"] for r in still_open
                     if r["last_mark_kind"] == "EXECUTABLE")
    events = Counter(r["kind"] for r in dbs.paper_ledger.execute("SELECT kind FROM paper_events"))
    blocked: Counter[str] = Counter()
    for r in dbs.paper_ledger.execute(
            "SELECT payload_json FROM paper_events WHERE kind IN ('ENTRY_BLOCKED', 'ENTRY_REJECTED_SIZE')"):
        blocked.update(json.loads(r["payload_json"])["reasons"])
    chosen = Counter(json.loads(r["payload_json"])["chosen"] for r in
                     dbs.paper_ledger.execute("SELECT payload_json FROM paper_events WHERE kind = 'DECISION'"))
    jupiter = {r["bucket"]: int(r["allocated"]) for r in dbs.runtime.execute(
        "SELECT bucket, allocated FROM rate_totals WHERE bucket LIKE 'jupiter:%'")}
    realized = ledger.realized_pnl_lamports()
    base = ledger.base_lamports()
    guardian_age = heartbeat_age(dbs.runtime, "guardian", now)
    return {
        "initialized": True, "book_started_at": meta["book_started_at"], "sol_usd_at_start": meta["sol_usd_at_start"],
        "cash_sol": _sol(ledger.cash_lamports()), "deposit_sol": _sol(int(meta["deposit_lamports"])),
        "sizing_capital_sol": _sol(base + max(0, realized)), "base_sol": _sol(base),
        "realized_pnl_sol": {name: _sol(value) for name, value in profiles.items()} or {"conservative": _sol(0)},
        "unrealized_executable_sol": _sol(unrealized),
        "closed_trades": len(closed), "open_positions": len(still_open),
        "open": [{"id": r["id"], "strategy": r["strategy_id"], "state": r["state"], "opened_at": r["opened_at"],
                  "ticket_sol": _sol(r["ticket_lamports"]),
                  "mark_sol": None if r["last_mark_lamports"] is None else _sol(r["last_mark_lamports"]),
                  "mark_kind": r["last_mark_kind"]} for r in still_open],
        "by_strategy": strategies,
        "exit_reasons": dict(Counter(r["exit_reason"] for r in closed)),
        "decisions": dict(chosen), "events": dict(events), "entry_blocked_reasons": dict(blocked),
        "reconciliation": ledger.reconcile(),
        "halted": ledger.halted(), "stop_file": stop_file_set(dbs),
        "guardian_heartbeat_age_seconds": None if guardian_age is None else round(guardian_age, 1),
        "jupiter_requests_allocated": jupiter,
        "replica_100_usd_account": replica(closed, still_open, loaded, base),
        "realized_vs_estimated": "realized P&L uses closed conservative fills; unrealized uses executable sell quotes only",
        "first_trade_at": rows[0]["opened_at"] if rows else None,
        "hours_since_start": round((now - parse_iso8601(meta["book_started_at"])).total_seconds() / 3600, 2),
    }
