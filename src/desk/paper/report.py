"""``desk paper-report``: learning-book results by profile/strategy/exit, reconciliation, and the reference replica."""

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
    """Estimate what the real reference account would have done with the same signals (PLAN §13.1.1, §13.2).

    Opens and closes are replayed on one timeline (a close at the same instant is processed first). The account
    starts with the reference base, sizes at fraction x min(equity, base) (no evidence-gated growth yet), and refuses an entry
    for: cumulative drawdown halt (permanent), daily realized loss, daily equity drawdown, trades/day, open
    positions, total exposure, free cash, or fee floor/minimum ticket. Each UTC day's limits use that day's
    start equity and only P&L realized on that day. Returns scale linearly from the learning-book fill; fixed
    fees are charged per trade unscaled. It is an estimate, labelled as such.
    """
    sizing, limits = loaded.settings.sizing, loaded.settings.limits
    timeline: list[tuple[str, int, str, dict[str, Any]]] = []
    for position in [*closed, *still_open]:
        timeline.append((position["opened_at"], 1, position["id"], position))
        if position.get("closed_at"):
            timeline.append((position["closed_at"], 0, position["id"], position))
    timeline.sort(key=lambda item: (item[0], item[1], item[2]))
    equity = peak = base_lamports
    active: dict[str, tuple[int, int]] = {}                 # position id -> (replica ticket, replica pnl)
    skipped: Counter[str] = Counter()
    taken, day, day_start, day_trades, day_realized, halted = 0, "", equity, 0, 0, False
    max_drawdown = Decimal(0)
    rent = loaded.settings.paper.token_account_rent_lamports
    for at, kind, position_id, position in timeline:
        if at[:10] != day:
            day, day_start, day_trades, day_realized = at[:10], equity, 0, 0
        if kind == 0:                                       # close
            if position_id in active:
                _, pnl = active.pop(position_id)
                equity += pnl
                day_realized += pnl
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, Decimal(peak - equity) / Decimal(peak))
                if equity <= peak * (1 - limits.cumulative_drawdown_fraction):
                    halted = True                           # until a manual reset: never lifted here
            continue
        ticket = floor_int(Decimal(min(equity, base_lamports)) * sizing.fixed_fraction)
        fixed = position["entry_fees_lamports"] + (position["exit_fees_lamports"] or 0)
        exposure = sum(t for t, _ in active.values())
        reason = (
            "CUMULATIVE_DRAWDOWN_HALT" if halted
            else "DAILY_LOSS_LIMIT" if day_realized <= -day_start * limits.daily_realized_loss_fraction
            else "DAILY_DRAWDOWN" if equity <= day_start * (1 - limits.daily_equity_drawdown_fraction)
            else "TRADES_PER_DAY" if day_trades >= limits.max_trades_per_day
            else "POSITION_LIMIT" if len(active) >= limits.max_open_positions
            else "EXPOSURE_LIMIT" if exposure + ticket > equity * limits.max_total_exposure_fraction
            else "INSUFFICIENT_CASH" if equity - exposure - len(active) * rent < ticket + fixed + rent
            else "FEE_FLOOR_OR_MINIMUM" if (ticket < sol_to_lamports(sizing.min_ticket_sol)
                                            or Decimal(fixed) > Decimal(ticket) * sizing.max_fixed_cost_fraction)
            else None)
        if reason:
            skipped[reason] += 1
            continue
        taken += 1
        day_trades += 1
        pnl = 0
        if position.get("closed_at"):
            gross = Decimal(position["exit_lamports"] - position["ticket_lamports"]) / Decimal(position["ticket_lamports"])
            pnl = floor_int(gross * ticket) - fixed
        active[position_id] = (ticket, pnl)
    open_ids = {position["id"] for position in still_open}
    open_at_end = sum(1 for position_id in active if position_id in open_ids)
    return {"start_lamports": base_lamports, "equity_lamports": equity, "equity_sol": _sol(equity),
            "return": str((Decimal(equity - base_lamports) / Decimal(base_lamports)).quantize(Decimal("0.0001"))),
            "trades_taken": taken, "skipped": dict(skipped), "open_at_end": open_at_end,
            "max_drawdown": str(max_drawdown.quantize(Decimal("0.0001"))), "halted_by_cumulative_drawdown": halted,
            "note": "Counterfactual at the current reference base using historical fills; past tickets unchanged. "
                    "Linear size approximation, fixed fees unscaled; missing exits are not profits."}


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
        "base_usd": ledger.reference_capital()["base_usd"], "reference_capital": ledger.reference_capital(),
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
        "account_replica": replica(closed, still_open, loaded, base),
        "realized_vs_estimated": "realized P&L uses closed conservative fills; unrealized uses executable sell quotes only",
        "first_trade_at": rows[0]["opened_at"] if rows else None,
        "hours_since_start": round((now - parse_iso8601(meta["book_started_at"])).total_seconds() / 3600, 2),
    }
