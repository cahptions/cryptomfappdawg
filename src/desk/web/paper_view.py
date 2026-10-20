"""PAPER dashboard summary with SQL aggregates and a streamed, bounded-state replica."""

from __future__ import annotations

from collections import Counter
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


def _sol(value: int) -> str:
    return f"{Decimal(value) / 10 ** 9:.6f}"


class _ReturnMean:
    """SQLite aggregate retaining a Decimal sum and count, never a list of returns."""

    def __init__(self) -> None:
        self.total = Decimal(0)
        self.count = 0

    def step(self, value: int, /, ticket: int = 1) -> None:
        self.total += Decimal(value) / Decimal(ticket)
        self.count += 1

    def finalize(self) -> int:
        return int((self.total / self.count).quantize(Decimal("0.0001")) * 10_000)


def _replica(dbs: Databases, loaded: LoadedConfig, base: int) -> dict[str, Any]:
    """Same fixed-reference-capital replica policy as the CLI, with only accepted open tickets in memory."""
    sizing, limits = loaded.settings.sizing, loaded.settings.limits
    timeline = dbs.paper_ledger.execute(
        "SELECT opened_at AS at,1 AS event_kind,id,ticket_lamports,entry_fees_lamports,exit_fees_lamports,"
        "exit_lamports,closed_at,state FROM positions UNION ALL "
        "SELECT closed_at AS at,0 AS event_kind,id,ticket_lamports,entry_fees_lamports,exit_fees_lamports,"
        "exit_lamports,closed_at,state FROM positions WHERE state='CLOSED' "
        "ORDER BY at,event_kind,id")
    equity = peak = base
    active: dict[str, tuple[int, int, bool]] = {}
    skipped: Counter[str] = Counter()
    taken, day, day_start, day_trades, day_realized, halted = 0, "", equity, 0, 0, False
    max_drawdown = Decimal(0)
    rent = loaded.settings.paper.token_account_rent_lamports
    for row in timeline:
        at, kind, position_id = row["at"], row["event_kind"], row["id"]
        if at[:10] != day:
            day, day_start, day_trades, day_realized = at[:10], equity, 0, 0
        if kind == 0:
            if position_id in active:
                _, pnl, _ = active.pop(position_id)
                equity += pnl
                day_realized += pnl
                peak = max(peak, equity)
                max_drawdown = max(max_drawdown, Decimal(peak - equity) / Decimal(peak))
                if equity <= peak * (1 - limits.cumulative_drawdown_fraction):
                    halted = True
            continue
        ticket = floor_int(Decimal(min(equity, base)) * sizing.fixed_fraction)
        fixed = row["entry_fees_lamports"] + (row["exit_fees_lamports"] or 0)
        exposure = sum(item[0] for item in active.values())
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
        if row["closed_at"]:
            gross = Decimal(row["exit_lamports"] - row["ticket_lamports"]) / Decimal(row["ticket_lamports"])
            pnl = floor_int(gross * ticket) - fixed
        active[position_id] = (ticket, pnl, row["state"] != "CLOSED")
    return {"start_lamports": base, "equity_lamports": equity, "equity_sol": _sol(equity),
            "return": str((Decimal(equity - base) / Decimal(base)).quantize(Decimal("0.0001"))),
            "trades_taken": taken, "skipped": dict(skipped), "open_at_end": sum(item[2] for item in active.values()),
            "max_drawdown": str(max_drawdown.quantize(Decimal("0.0001"))), "halted_by_cumulative_drawdown": halted,
            "note": "Counterfactual at the current reference base using historical fills; past tickets unchanged. "
                    "Linear size approximation, fixed fees unscaled; missing exits are not profits."}


def paper_snapshot(dbs: Databases, loaded: LoadedConfig, now: datetime) -> dict[str, Any]:
    """Read committed evidence without building lists proportional to historical trades/events."""
    ledger = PaperLedger(dbs.paper_ledger)
    if not ledger.initialized():
        return {"initialized": False, "note": "no PAPER book yet; start one with `desk paper`"}
    conn = dbs.paper_ledger
    meta = ledger.meta()
    totals = conn.execute(
        "SELECT COUNT(*) AS trades, SUM(state='CLOSED') AS closed, SUM(state!='CLOSED') AS opened, "
        "MIN(opened_at) AS first_trade, "
        "COALESCE(SUM(CASE WHEN state!='CLOSED' AND last_mark_kind='EXECUTABLE' "
        "THEN COALESCE(last_mark_lamports,0)-ticket_lamports ELSE 0 END),0) AS unrealized, "
        "COALESCE(SUM(-ticket_lamports-entry_fees_lamports-rent_lamports-exit_failed_fees_lamports "
        "+CASE WHEN state='CLOSED' THEN exit_lamports-exit_fees_lamports+rent_lamports ELSE 0 END),0) AS net_cash "
        "FROM positions").fetchone()
    closed_count = int(totals["closed"] or 0)
    profile_totals = conn.execute(
        "SELECT COALESCE(SUM(json_extract(profiles_json,'$.observed.pnl_lamports')),0), "
        "COALESCE(SUM(json_extract(profiles_json,'$.conservative.pnl_lamports')),0), "
        "COALESCE(SUM(json_extract(profiles_json,'$.stress.pnl_lamports')),0) FROM positions WHERE state='CLOSED'").fetchone()
    profiles = {name: _sol(int(value)) for name, value in zip(("observed", "conservative", "stress"), profile_totals,
                                                          strict=True)} if closed_count else {"conservative": _sol(0)}
    conn.create_aggregate("desk_return_mean", 2, _ReturnMean)
    strategies = {}
    for row in conn.execute(
            "SELECT strategy_id,COUNT(*) AS trades,SUM(realized_pnl_lamports>0) AS wins, "
            "SUM(realized_pnl_lamports) AS pnl,desk_return_mean(realized_pnl_lamports,ticket_lamports) AS mean_return "
            "FROM positions WHERE state='CLOSED' GROUP BY strategy_id ORDER BY strategy_id"):
        strategies[row["strategy_id"]] = {"trades": row["trades"],
            "win_rate": str(Decimal(row["wins"]) / row["trades"]), "pnl_sol": _sol(row["pnl"]),
            "mean_return": f"{Decimal(row['mean_return']) / 10_000:.4f}"}
    events = {row[0]: row[1] for row in conn.execute("SELECT kind,COUNT(*) FROM paper_events GROUP BY kind")}
    blocked = {row[0]: row[1] for row in conn.execute(
        "SELECT reason.value,COUNT(*) FROM paper_events,json_each(payload_json,'$.reasons') AS reason "
        "WHERE kind IN ('ENTRY_BLOCKED','ENTRY_REJECTED_SIZE') GROUP BY reason.value")}
    chosen = {row[0]: row[1] for row in conn.execute(
        "SELECT json_extract(payload_json,'$.chosen') AS chosen,COUNT(*) FROM paper_events "
        "WHERE kind='DECISION' GROUP BY chosen")}
    exits = {row[0]: row[1] for row in conn.execute(
        "SELECT exit_reason,COUNT(*) FROM positions WHERE state='CLOSED' GROUP BY exit_reason")}
    positions = [{"id": row["id"], "strategy": row["strategy_id"], "state": row["state"],
                  "opened_at": row["opened_at"], "ticket_sol": _sol(row["ticket_lamports"]),
                  "mark_sol": None if row["last_mark_lamports"] is None else _sol(row["last_mark_lamports"]),
                  "mark_kind": row["last_mark_kind"]} for row in conn.execute(
                      "SELECT id,strategy_id,state,opened_at,ticket_lamports,last_mark_lamports,last_mark_kind "
                      "FROM positions WHERE state!='CLOSED' ORDER BY opened_at,id LIMIT 100")]
    base, realized, cash = ledger.base_lamports(), ledger.realized_pnl_lamports(), ledger.cash_lamports()
    recomputed = int(meta.get("deposit_lamports", "0")) + int(totals["net_cash"])
    guardian_age = heartbeat_age(dbs.runtime, "guardian", now)
    return {"initialized": True, "book_started_at": meta["book_started_at"], "sol_usd_at_start": meta["sol_usd_at_start"],
            "base_usd": ledger.reference_capital()["base_usd"], "reference_capital": ledger.reference_capital(),
            "cash_sol": _sol(cash), "deposit_sol": _sol(int(meta["deposit_lamports"])),
            "sizing_capital_sol": _sol(base + max(0, realized)), "base_sol": _sol(base), "realized_pnl_sol": profiles,
            "unrealized_executable_sol": _sol(totals["unrealized"]), "closed_trades": closed_count,
            "open_positions": int(totals["opened"] or 0), "open": positions, "by_strategy": strategies,
            "exit_reasons": exits, "decisions": chosen, "events": events, "entry_blocked_reasons": blocked,
            "reconciliation": {"journal_lamports": cash, "recomputed_lamports": recomputed,
                               "difference_lamports": cash - recomputed},
            "halted": ledger.halted(), "stop_file": stop_file_set(dbs),
            "guardian_heartbeat_age_seconds": None if guardian_age is None else round(guardian_age, 1),
            "jupiter_requests_allocated": {row[0]: int(row[1]) for row in dbs.runtime.execute(
                "SELECT bucket,allocated FROM rate_totals WHERE bucket LIKE 'jupiter:%'")},
            "account_replica": _replica(dbs, loaded, base),
            "realized_vs_estimated": "realized P&L uses closed conservative fills; unrealized uses executable sell quotes only",
            "first_trade_at": totals["first_trade"],
            "hours_since_start": round((now - parse_iso8601(meta["book_started_at"])).total_seconds() / 3600, 2)}
